# python/models/flat_file_api_xgboost.py

import pandas as pd
import numpy as np
from xgboost import XGBRegressor


def train_and_forecast_flat_file_with_api(
    flat_df,
    dcsid,
    mic,
    pricetype=None,
    horizon=30,
    api_factors=None
):
    """
    Forecast a flat-file target using external API/HANA data as factors.

    Target:
        PRICE from ZRISK_FLATFILE

    Example factor:
        WTI from EIA WTI API/HANA table

    Parameters
    ----------
    flat_df : pandas.DataFrame
        Flat-file data containing DCSID, MIC, PRICEDATE and PRICE.

    dcsid : str
        Target DCSID.

    mic : str
        Target MIC.

    pricetype : str, optional
        Optional PRICETYPE filter.

    horizon : int
        Number of future periods to forecast.

    api_factors : list of dictionaries
        Example:

        [
            {
                "df": wti_df,
                "value_column": "VALUE",
                "name": "WTI"
            }
        ]

    Returns
    -------
    list
        Forecast records containing DATE and PREDICTED_VALUE.
    """

    # ============================================================
    # 1. VALIDATE INPUT
    # ============================================================

    if flat_df is None or flat_df.empty:
        raise ValueError("Flat-file data is empty.")

    if horizon <= 0:
        raise ValueError("Forecast horizon must be greater than 0.")

    if api_factors is None:
        api_factors = []

    # Work on a copy so original dataframe is not modified
    df = flat_df.copy()

    # ============================================================
    # 2. NORMALIZE COLUMN NAMES
    # ============================================================

    df.columns = [str(col).strip().upper() for col in df.columns]

    required_columns = [
        "DCSID",
        "MIC",
        "PRICEDATE",
        "PRICE"
    ]

    missing_columns = [
        col for col in required_columns
        if col not in df.columns
    ]

    if missing_columns:
        raise ValueError(
            f"Missing required flat-file columns: {missing_columns}"
        )

    # ============================================================
    # 3. FILTER TARGET
    # ============================================================

    df["DCSID"] = df["DCSID"].astype(str).str.strip()
    df["MIC"] = df["MIC"].astype(str).str.strip()

    target_df = df[
        (df["DCSID"] == str(dcsid).strip()) &
        (df["MIC"] == str(mic).strip())
    ].copy()

    # Optional PRICETYPE filter
    if pricetype is not None and "PRICETYPE" in target_df.columns:
        target_df = target_df[
            target_df["PRICETYPE"].astype(str).str.strip()
            == str(pricetype).strip()
        ].copy()

    if target_df.empty:
        raise ValueError(
            f"No flat-file data found for DCSID={dcsid}, MIC={mic}"
        )

    # ============================================================
    # 4. PREPARE TARGET DATA
    # ============================================================

    target_df["PRICEDATE"] = pd.to_datetime(
        target_df["PRICEDATE"],
        errors="coerce"
    )

    target_df["PRICE"] = pd.to_numeric(
        target_df["PRICE"],
        errors="coerce"
    )

    target_df = target_df.dropna(
        subset=["PRICEDATE", "PRICE"]
    )

    if target_df.empty:
        raise ValueError(
            "No valid PRICEDATE/PRICE records available after cleaning."
        )

    # If duplicate dates exist, use the last value
    target_df = (
        target_df
        .sort_values("PRICEDATE")
        .drop_duplicates(
            subset=["PRICEDATE"],
            keep="last"
        )
    )

    # Rename for modelling
    target_df = target_df[
        ["PRICEDATE", "PRICE"]
    ].rename(
        columns={
            "PRICEDATE": "DATE",
            "PRICE": "TARGET"
        }
    )

    target_df = target_df.sort_values("DATE").reset_index(drop=True)

    # ============================================================
    # 5. ADD API FACTORS
    # ============================================================

    for factor in api_factors:

        if "df" not in factor:
            raise ValueError(
                "Each API factor must contain a 'df'."
            )

        if "value_column" not in factor:
            raise ValueError(
                "Each API factor must contain a 'value_column'."
            )

        factor_df = factor["df"].copy()

        value_column = str(
            factor["value_column"]
        ).strip().upper()

        factor_name = factor.get(
            "name",
            value_column
        )

        factor_name = str(
            factor_name
        ).strip().upper()

        factor_df.columns = [
            str(col).strip().upper()
            for col in factor_df.columns
        ]

        # --------------------------------------------------------
        # Find date column
        # --------------------------------------------------------

        if "DATE" in factor_df.columns:
            factor_date_column = "DATE"

        elif "PRICEDATE" in factor_df.columns:
            factor_date_column = "PRICEDATE"

        else:
            raise ValueError(
                f"Could not find DATE column for API factor {factor_name}."
            )

        if value_column not in factor_df.columns:
            raise ValueError(
                f"Column {value_column} not found in API factor {factor_name}."
            )

        # --------------------------------------------------------
        # Clean factor
        # --------------------------------------------------------

        factor_df[factor_date_column] = pd.to_datetime(
            factor_df[factor_date_column],
            errors="coerce"
        )

        factor_df[value_column] = pd.to_numeric(
            factor_df[value_column],
            errors="coerce"
        )

        factor_df = factor_df.dropna(
            subset=[
                factor_date_column,
                value_column
            ]
        )

        factor_df = factor_df[
            [factor_date_column, value_column]
        ].copy()

        factor_df = factor_df.rename(
            columns={
                factor_date_column: "DATE",
                value_column: factor_name
            }
        )

        factor_df = (
            factor_df
            .sort_values("DATE")
            .drop_duplicates(
                subset=["DATE"],
                keep="last"
            )
        )

        # --------------------------------------------------------
        # Merge with target
        # --------------------------------------------------------

        target_df = pd.merge(
            target_df,
            factor_df,
            on="DATE",
            how="left"
        )

        # API data may only contain business days while the
        # flat-file target may contain calendar days.
        #
        # Therefore fill the missing factor values using
        # the latest available API value.
        target_df[factor_name] = (
            target_df[factor_name]
            .ffill()
            .bfill()
        )

    # ============================================================
    # 6. CHECK API FACTOR DATA
    # ============================================================

    factor_names = []

    for factor in api_factors:
        factor_names.append(
            str(
                factor.get(
                    "name",
                    factor["value_column"]
                )
            ).strip().upper()
        )

    for factor_name in factor_names:

        if target_df[factor_name].isna().all():
            raise ValueError(
                f"No usable data available for API factor {factor_name}."
            )

    # ============================================================
    # 7. CREATE LAG FEATURES
    # ============================================================

    # Target lag features
    target_df["TARGET_LAG_1"] = (
        target_df["TARGET"].shift(1)
    )

    target_df["TARGET_LAG_7"] = (
        target_df["TARGET"].shift(7)
    )

    # API factor lag features
    for factor_name in factor_names:

        target_df[f"{factor_name}_LAG_1"] = (
            target_df[factor_name].shift(1)
        )

        target_df[f"{factor_name}_LAG_7"] = (
            target_df[factor_name].shift(7)
        )

    # ============================================================
    # 8. REMOVE ROWS WITH MISSING FEATURES
    # ============================================================

    feature_columns = [
        "TARGET_LAG_1",
        "TARGET_LAG_7"
    ]

    for factor_name in factor_names:

        feature_columns.extend([
            factor_name,
            f"{factor_name}_LAG_1",
            f"{factor_name}_LAG_7"
        ])

    model_df = target_df.dropna(
        subset=feature_columns + ["TARGET"]
    ).copy()

    if model_df.empty:
        raise ValueError(
            "No training rows available after creating lag features."
        )

    if len(model_df) < 20:
        raise ValueError(
            f"Not enough training data. "
            f"Only {len(model_df)} usable rows available."
        )

    # ============================================================
    # 9. TRAINING DATA
    # ============================================================

    X = model_df[feature_columns]
    y = model_df["TARGET"]

    # ============================================================
    # 10. XGBOOST MODEL
    # ============================================================

    model = XGBRegressor(
        n_estimators=100,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        objective="reg:squarederror",
        random_state=42,
        n_jobs=1,
        tree_method="hist"
    )

    model.fit(X, y)

    # ============================================================
    # 11. PREPARE RECURSIVE FORECAST
    # ============================================================

    history = target_df[
        ["DATE", "TARGET"] + factor_names
    ].copy()

    history = history.sort_values("DATE").reset_index(drop=True)

    last_date = history["DATE"].iloc[-1]

    # ------------------------------------------------------------
    # Future dates
    # ------------------------------------------------------------

    future_dates = pd.date_range(
        start=last_date + pd.Timedelta(days=1),
        periods=horizon,
        freq="D"
    )

    # ------------------------------------------------------------
    # Future API factor values
    # ------------------------------------------------------------
    #
    # We do NOT forecast WTI here.
    #
    # For MVP, future API factor values are held at their
    # latest available value.
    # ------------------------------------------------------------

    latest_factor_values = {}

    for factor_name in factor_names:

        latest_value = (
            history[factor_name]
            .dropna()
            .iloc[-1]
        )

        latest_factor_values[factor_name] = latest_value

    # ============================================================
    # 12. RECURSIVE FORECAST
    # ============================================================

    predictions = []

    target_history = list(
        history["TARGET"].astype(float)
    )

    factor_history = {}

    for factor_name in factor_names:

        factor_history[factor_name] = list(
            history[factor_name].astype(float)
        )

    for future_date in future_dates:

        # --------------------------------------------------------
        # Target lag 1
        # --------------------------------------------------------

        lag_1 = target_history[-1]

        # --------------------------------------------------------
        #  Target lag 7
        # --------------------------------------------------------

        if len(target_history) >= 7:
            lag_7 = target_history[-7]
        else:
            lag_7 = target_history[0]

        feature_values = [
            lag_1,
            lag_7
        ]

        # --------------------------------------------------------
        # API factor features
        # --------------------------------------------------------

        for factor_name in factor_names:

            latest_factor = latest_factor_values[
                factor_name
            ]

            # Future factor is held constant
            factor_history[factor_name].append(
                latest_factor
            )

            factor_lag_1 = (
                factor_history[factor_name][-2]
                if len(factor_history[factor_name]) >= 2
                else latest_factor
            )

            if len(factor_history[factor_name]) >= 8:
                factor_lag_7 = (
                    factor_history[factor_name][-8]
                )
            else:
                factor_lag_7 = latest_factor

            feature_values.extend([
                latest_factor,
                factor_lag_1,
                factor_lag_7
            ])

        # --------------------------------------------------------
        # Create model input
        # --------------------------------------------------------

        X_future = pd.DataFrame(
            [feature_values],
            columns=feature_columns
        )

        # --------------------------------------------------------
        # Predict
        # --------------------------------------------------------

        prediction = model.predict(
            X_future
        )[0]

        prediction = float(prediction)

        # --------------------------------------------------------
        # Add prediction to history
        # --------------------------------------------------------

        target_history.append(
            prediction
        )

        predictions.append({
             "date": future_date.strftime("%Y-%m-%d"),
            "predicted_value": round(prediction, 4)
        })

    # ============================================================
    # 13. RETURN FORECAST
    # ============================================================

    return predictions