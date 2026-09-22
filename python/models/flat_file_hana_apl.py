import logging
from numbers import Integral
from uuid import uuid4

import numpy as np
import pandas as pd


LOGGER = logging.getLogger(__name__)

# Separate workspace for APL staging and internal database objects.
APL_WORK_SCHEMA = "TIME_SERIES_FORECAST_APL_WORK"


def train_and_forecast_hana_apl(
    df,
    dcsid,
    mic,
    credentials,
    pricetype=None,
    horizon=30,
    forecast_start_date=None,
):
    """Return daily APL forecasts as date/predicted_value dictionaries.

    horizon is a number of calendar days. The job worker converts the
    selected day/week/month horizon before calling this function.
    """
    if isinstance(horizon, bool) or not isinstance(horizon, Integral):
        raise ValueError("Horizon must be a positive integer")
    if horizon < 1:
        raise ValueError("Horizon must be a positive integer")
    horizon = int(horizon)

    required = {"DCSID", "MIC", "PRICEDATE", "PRICE"}
    if pricetype is not None:
        required.add("PRICETYPE")
    missing = required.difference(df.columns)
    if missing:
        raise ValueError("Missing columns: " + ", ".join(sorted(missing)))

    selected = (
        df["DCSID"].astype(str).str.strip().eq(str(dcsid).strip())
        & df["MIC"].astype(str).str.strip().eq(str(mic).strip())
    )
    if pricetype is not None:
        selected &= (
            df["PRICETYPE"].astype(str).str.strip()
            .eq(str(pricetype).strip())
        )

    data = df.loc[selected, ["PRICEDATE", "PRICE"]].copy()
    data["PRICEDATE"] = pd.to_datetime(data["PRICEDATE"], errors="coerce")
    if data["PRICEDATE"].dt.tz is not None:
        raise ValueError("PRICEDATE must contain dates without a timezone")
    data["PRICEDATE"] = data["PRICEDATE"].dt.normalize()
    data["PRICE"] = pd.to_numeric(data["PRICE"], errors="coerce")
    data = data.dropna(subset=["PRICEDATE", "PRICE"])
    data = data[np.isfinite(data["PRICE"].astype(float))]

    start = None
    if forecast_start_date is not None:
        start = pd.to_datetime(forecast_start_date, errors="coerce")
        if pd.isna(start) or start.tzinfo is not None:
            raise ValueError("Forecast start must be a valid date without a timezone")
        start = start.normalize()
        # Prevent training on prices from the forecast period.
        data = data[data["PRICEDATE"] < start]

    if data.empty:
        raise ValueError("No valid historical prices for the selected series")

    # Identical duplicates are harmless; conflicting prices need a
    # more specific series selection instead of an arbitrary choice.
    counts = data.groupby("PRICEDATE")["PRICE"].nunique()
    if counts.gt(1).any():
        raise ValueError(
            "Multiple prices exist on the same date for this series. "
            "Select a single price type/maturity series before forecasting."
        )
    data = data.sort_values("PRICEDATE").drop_duplicates("PRICEDATE")
    if len(data) < 30:
        raise ValueError(
            f"At least 30 historical dates are required; found {len(data)}"
        )

    last_date = data["PRICEDATE"].iloc[-1]
    if start is None:
        start = last_date + pd.Timedelta(days=1)
    expected_dates = pd.date_range(start, periods=horizon, freq="D")
    apl_horizon = int((expected_dates[-1] - last_date).days)

    # APL receives a regular daily series. Fill internal missing days
    # with the most recent known price, never with a later price.
    history = (
        data.set_index("PRICEDATE")["PRICE"]
        .astype(float)
        .asfreq("D")
        .ffill()
        .rename("PRICES")
        .rename_axis("DATE")
        .reset_index()
    )

    required_credentials = ("host", "port", "user", "password", "schema")
    if any(not credentials.get(key) for key in required_credentials):
        raise ValueError("HANA service credentials are incomplete")

    # Import only when APL is selected, keeping XGBoost independent.
    try:
        from hana_ml import dataframe
        from hana_ml.algorithms.apl.time_series import AutoTimeSeries
    except ImportError as error:
        raise RuntimeError(
            "HANA APL requires hana-ml. Install python/requirements.txt."
        ) from error

    # Use the existing service login with the dedicated APL workspace.
    # The job worker continues reading and saving in the application schema.
    schema = APL_WORK_SCHEMA
    table_name = "APL_INPUT_" + uuid4().hex.upper()
    context = None
    upload_started = False

    try:
        context = dataframe.ConnectionContext(
            address=credentials["host"],
            port=int(credentials["port"]),
            user=credentials["user"],
            password=credentials["password"],
            encrypt=True,
            sslValidateCertificate=True,
            currentSchema=schema,
        )

        upload_started = True
        hana_data = dataframe.create_dataframe_from_pandas(
            connection_context=context,
            pandas_df=history,
            table_name=table_name,
            schema=schema,
            force=False,
            replace=False,
            table_structure={"DATE": "TIMESTAMP", "PRICES": "DOUBLE"},
            disable_progressbar=True,
        )

        model = AutoTimeSeries(
            time_column_name="DATE",
            target="PRICES",
            horizon=apl_horizon,
            with_extra_predictable=False,
        )
        result = model.fit_predict(data=hana_data).collect()
        if not {"DATE", "PREDICTED"}.issubset(result.columns):
            raise RuntimeError("APL did not return DATE and PREDICTED columns")

        result["DATE"] = pd.to_datetime(result["DATE"], errors="coerce")
        result["PREDICTED"] = pd.to_numeric(result["PREDICTED"], errors="coerce")
        future = result.loc[
            result["DATE"].between(expected_dates[0], expected_dates[-1]),
            ["DATE", "PREDICTED"],
        ].sort_values("DATE")

        if future["DATE"].duplicated().any():
            raise RuntimeError("APL returned duplicate forecast dates")
        if not pd.DatetimeIndex(future["DATE"]).equals(expected_dates):
            raise RuntimeError("APL did not return every requested daily forecast date")
        if not np.isfinite(future["PREDICTED"].astype(float)).all():
            raise RuntimeError("APL returned invalid forecast prices")

        return [
            {
                "date": date.strftime("%Y-%m-%d"),
                "predicted_value": round(float(value), 4),
            }
            for date, value in future.itertuples(index=False, name=None)
        ]
    finally:
        if context is not None:
            try:
                # Also clean up if upload created a table but then failed.
                if upload_started and context.has_table(table_name, schema=schema):
                    context.drop_table(table_name, schema=schema)
            except Exception:
                LOGGER.exception("Could not remove APL staging table %s", table_name)
            finally:
                try:
                    context.close()
                except Exception:
                    LOGGER.exception("Could not close the APL connection")
