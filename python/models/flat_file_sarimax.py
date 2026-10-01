import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX


def train_and_forecast_flat_file_sarimax(
    df,
    wti_history,
    dcsid,
    mic,
    pricetype,
    horizon,
    forecast_start_date
):
    """
    Train SARIMAX using PRICE as the target and WTI as an
    external factor.

    MVP design:
        Target     = PRICE
        Exogenous  = WTI
        Order      = (5, 1, 0)
        Seasonal   = disabled
    """

    # ------------------------------------------------------------
    # 1. Validate inputs
    # ------------------------------------------------------------

    if df is None or df.empty:
        raise ValueError("Flat-file data is empty")

    if wti_history is None or wti_history.empty:
        raise ValueError("WTI history is empty")

    if not dcsid:
        raise ValueError("DCSID is required for SARIMAX")

    if not mic:
        raise ValueError("MIC is required for SARIMAX")

    if not pricetype:
        raise ValueError("PRICETYPE is required for SARIMAX")

    if horizon <= 0:
        raise ValueError("SARIMAX horizon must be positive")

    # ------------------------------------------------------------
    # 2. Copy input data
    # ------------------------------------------------------------

    data = df.copy()
    wti = wti_history.copy()

    # ------------------------------------------------------------
    # 3. Normalize flat-file fields
    # ------------------------------------------------------------

    data["DCSID"] = data["DCSID"].astype(str).str.strip()
    data["MIC"] = data["MIC"].astype(str).str.strip()
    data["PRICETYPE"] = data["PRICETYPE"].astype(str).str.strip()

    # ------------------------------------------------------------
    # 4. Select target instrument
    # ------------------------------------------------------------

    selected = data[
        (data["DCSID"] == str(dcsid).strip())
        & (data["MIC"] == str(mic).strip())
        & (data["PRICETYPE"] == str(pricetype).strip())
    ].copy()

    if selected.empty:
        raise ValueError(
            f"No flat-file data found for "
            f"{dcsid} / {mic} / {pricetype}"
        )

    # ------------------------------------------------------------
    # 5. Prepare target date and PRICE
    # ------------------------------------------------------------

    selected["PRICEDATE"] = pd.to_datetime(
        selected["PRICEDATE"],
        errors="coerce"
    )

    selected["PRICE"] = pd.to_numeric(
        selected["PRICE"],
        errors="coerce"
    )

    selected = selected.dropna(
        subset=["PRICEDATE", "PRICE"]
    )

    if selected.empty:
        raise ValueError(
            "No valid PRICEDATE / PRICE observations "
            "available for SARIMAX"
        )

    # ------------------------------------------------------------
    # 6. Forecast start date
    # ------------------------------------------------------------

    forecast_start = pd.Timestamp(
        forecast_start_date
    ).normalize()

    if pd.isna(forecast_start):
        raise ValueError(
            "Invalid SARIMAX forecast start date"
        )

    # Only historical target data is allowed for training.
    selected = selected[
        selected["PRICEDATE"] < forecast_start
    ].copy()

    if selected.empty:
        raise ValueError(
            "No historical observations exist before "
            "the SARIMAX forecast start date"
        )

    # ------------------------------------------------------------
    # 7. Sort target data
    # ------------------------------------------------------------

    selected = selected.sort_values(
        "PRICEDATE"
    )

    # Keep the latest value if duplicate dates exist.
    selected = selected.drop_duplicates(
        subset=["PRICEDATE"],
        keep="last"
    )

    # ------------------------------------------------------------
    # 8. Create daily target series
    # ------------------------------------------------------------

    target_series = (
        selected
        .set_index("PRICEDATE")["PRICE"]
        .astype(float)
        .sort_index()
        .asfreq("D")
        .ffill()
        .dropna()
    )

    if len(target_series) < 30:
        raise ValueError(
            "Insufficient historical observations "
            "for SARIMAX"
        )

    # ------------------------------------------------------------
    # 9. Prepare WTI history
    # ------------------------------------------------------------

    if "DATE" not in wti.columns:
        raise ValueError(
            "WTI history must contain DATE"
        )

    if "VALUE" not in wti.columns:
        raise ValueError(
            "WTI history must contain VALUE"
        )

    wti["DATE"] = pd.to_datetime(
        wti["DATE"],
        errors="coerce"
    )

    wti["VALUE"] = pd.to_numeric(
        wti["VALUE"],
        errors="coerce"
    )

    wti = wti.dropna(
        subset=["DATE", "VALUE"]
    )

    if wti.empty:
        raise ValueError(
            "WTI history contains no usable values"
        )

    wti = (
        wti
        .sort_values("DATE")
        .drop_duplicates(
            subset=["DATE"],
            keep="last"
        )
        .set_index("DATE")["VALUE"]
        .astype(float)
        .sort_index()
        .asfreq("D")
        .ffill()
    )

    # ------------------------------------------------------------
    # 10. Align WTI with target history
    # ------------------------------------------------------------

    training_index = target_series.index

    wti_training = wti.reindex(
        training_index
    ).ffill()

    # Keep only dates where both PRICE and WTI exist.
    valid_mask = (
        wti_training.notna()
        & target_series.notna()
    )

    target_series = target_series.loc[
        valid_mask
    ]

    wti_training = wti_training.loc[
        valid_mask
    ]

    if len(target_series) < 30:
        raise ValueError(
            "Insufficient overlapping PRICE and WTI "
            "history for SARIMAX"
        )


    # ------------------------------------------------------------
    # 11. Build future WTI values
    # ------------------------------------------------------------

    future_dates = pd.date_range(
        start=forecast_start,
        periods=horizon,
        freq="D"
    )

    latest_wti_date = wti.dropna().index.max()

    if pd.isna(latest_wti_date):
        raise ValueError(
            "No latest WTI value available"
        )

    latest_wti_value = float(
        wti.loc[latest_wti_date]
    )

    future_wti = pd.Series(
        latest_wti_value,
        index=future_dates,
        name="WTI_CRUDE_USD_BBL"
    )

    # ------------------------------------------------------------
    # 12. Fit SARIMAX
    # ------------------------------------------------------------

    model = SARIMAX(
        endog=target_series,
        exog=wti_training.to_frame(),
        order=(5, 1, 0),
        seasonal_order=(0, 0, 0, 0),
        enforce_stationarity=False,
        enforce_invertibility=False
    )

    fitted_model = model.fit(
        disp=False
    )

    # ------------------------------------------------------------
    # 13. Forecast
    # ------------------------------------------------------------

    forecast_values = fitted_model.forecast(
        steps=horizon,
        exog=future_wti.to_frame()
    )

    # ------------------------------------------------------------
    # 14. Convert to standard application format
    # ------------------------------------------------------------

    forecasts = []

    for index, value in enumerate(
        forecast_values
    ):

        forecast_date = (
            forecast_start
            + pd.Timedelta(days=index)
        )

        predicted_value = float(value)

        if not pd.notna(predicted_value):
            raise ValueError(
                "SARIMAX produced a non-finite "
                "forecast value"
            )

        forecasts.append(
            {
                "date": forecast_date.strftime(
                    "%Y-%m-%d"
                ),
                "predicted_value": predicted_value
            }
        )

    return forecasts