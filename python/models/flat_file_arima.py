import pandas as pd
from statsmodels.tsa.arima.model import ARIMA


def train_and_forecast_flat_file_arima(
    df,
    dcsid,
    mic,
    pricetype,
    horizon,
    forecast_start_date
):
    """
    Train ARIMA on a selected flat-file time series
    and return forecast rows in the standard application format.
    This is univariate analysis, it uses only target values
    """

    # ------------------------------------------------------------
    # 1. Validate inputs
    # ------------------------------------------------------------

    if df is None or df.empty:
        raise ValueError("Flat-file data is empty")

    if not dcsid:
        raise ValueError("DCSID is required for ARIMA")

    if not mic:
        raise ValueError("MIC is required for ARIMA")

    if not pricetype:
        raise ValueError("PRICETYPE is required for ARIMA")

    if horizon <= 0:
        raise ValueError("ARIMA horizon must be positive")

    # ------------------------------------------------------------
    # 2. Copy data
    # ------------------------------------------------------------

    data = df.copy()

    # ------------------------------------------------------------
    # 3. Filter selected instrument
    # ------------------------------------------------------------

    data["DCSID"] = data["DCSID"].astype(str).str.strip()
    data["MIC"] = data["MIC"].astype(str).str.strip()
    data["PRICETYPE"] = data["PRICETYPE"].astype(str).str.strip()

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
    # 4. Prepare date and target
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
            "No valid PRICEDATE / PRICE observations available "
            "for ARIMA"
        )

    # ------------------------------------------------------------
    # 5. Remove observations at/after forecast start
    # ------------------------------------------------------------

    forecast_start = pd.Timestamp(
        forecast_start_date
    ).normalize()

    if pd.isna(forecast_start):
        raise ValueError(
            "Invalid ARIMA forecast start date"
        )

    selected = selected[
        selected["PRICEDATE"] < forecast_start
    ].copy()

    if selected.empty:
        raise ValueError(
            "No historical observations exist before "
            "the ARIMA forecast start date"
        )

    # ------------------------------------------------------------
    # 6. Sort chronologically
    # ------------------------------------------------------------

    selected = selected.sort_values(
        "PRICEDATE"
    )

    # ------------------------------------------------------------
    # 7. Reject duplicate dates
    # ------------------------------------------------------------

    if selected["PRICEDATE"].duplicated().any():
        raise ValueError(
            "Duplicate PRICEDATE values found for "
            f"{dcsid} / {mic} / {pricetype}"
        )

    # ------------------------------------------------------------
    # 8. Create ARIMA time series
    # ------------------------------------------------------------
    """
    series = selected.set_index(
        "PRICEDATE"
    )["PRICE"].astype(float)

    if len(series) < 30:
        raise ValueError(
            "Insufficient historical observations for ARIMA"
        )
    """
    # ------------------------------------------------------------
    # 8. Create daily ARIMA time series
    # ------------------------------------------------------------

    series = (
    selected
    .set_index("PRICEDATE")["PRICE"]
    .astype(float)
    .sort_index()
    )

    # Remove any duplicate dates before creating the daily series
    series = series[~series.index.duplicated(keep="last")]

    # ARIMA needs an explicit frequency.
    # The application forecasts one value for every calendar day,
    # so convert the historical series to daily frequency.
    series = series.asfreq("D")

    # Market-price files normally do not contain weekends/holidays.
    # Carry the latest available market price forward for those
    # non-trading days so the ARIMA series remains continuous.
    series = series.ffill()

    # Remove any leading missing values if the series starts with a gap.
    series = series.dropna()
        
    if len(series) < 30:
        raise ValueError(
            "Insufficient historical observations for ARIMA"
    )
    # ------------------------------------------------------------
    # 9. Fit ARIMA
    # ------------------------------------------------------------

    model = ARIMA(
        series,
        order=(5, 1, 0)
    )

    fitted_model = model.fit()

    # ------------------------------------------------------------
    # 10. Generate forecast
    # ------------------------------------------------------------

    forecast_values = fitted_model.forecast(
        steps=horizon
    )

    # ------------------------------------------------------------
    # 11. Convert forecast to standard result format
    # ------------------------------------------------------------

    forecasts = []

    for index, value in enumerate(forecast_values):

        forecast_date = (
            forecast_start
            + pd.Timedelta(days=index)
        )

        predicted_value = float(value)

        if not pd.notna(predicted_value):
            raise ValueError(
                "ARIMA produced a non-finite forecast value"
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