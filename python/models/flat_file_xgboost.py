import pandas as pd
from xgboost import XGBRegressor


# ============================================================
# Prepare flat-file target data
# ============================================================

def prepare_target_data(
    df,
    dcsid,
    mic,
    pricetype=None
):

    # --------------------------------------------------------
    # Check required columns
    # --------------------------------------------------------

    required_columns = [
        "DCSID",
        "MIC",
        "PRICEDATE",
        "PRICE"
    ]

    for column in required_columns:

        if column not in df.columns:

            raise Exception(
                f"Required column '{column}' not found in file"
            )

    # PRICETYPE is optional
    if pricetype is not None:

        if "PRICETYPE" not in df.columns:

            raise Exception(
                "PRICETYPE filter was provided, "
                "but PRICETYPE column is not present"
            )

    # --------------------------------------------------------
    # Filter requested series
    # --------------------------------------------------------

    filtered = df[
        (df["DCSID"].astype(str).str.strip() == str(dcsid).strip()) &
        (df["MIC"].astype(str).str.strip() == str(mic).strip())
    ]

    if pricetype is not None:

        filtered = filtered[
            filtered["PRICETYPE"].astype(str).str.strip()
            == str(pricetype).strip()
        ]

    # --------------------------------------------------------
    # Check filtered data
    # --------------------------------------------------------

    if filtered.empty:

        raise Exception(
            "No records found for the selected "
            "DCSID / MIC / PRICETYPE"
        )

    # --------------------------------------------------------
    # Convert date and target
    # --------------------------------------------------------

    filtered = filtered.copy()

    filtered["PRICEDATE"] = pd.to_datetime(
        filtered["PRICEDATE"],
        errors="coerce"
    )

    filtered["PRICE"] = pd.to_numeric(
        filtered["PRICE"],
        errors="coerce"
    )

    # Remove invalid records
    filtered = filtered.dropna(
        subset=["PRICEDATE", "PRICE"]
    )

    # --------------------------------------------------------
    # Sort chronologically
    # --------------------------------------------------------

    filtered = (
        filtered
        .sort_values("PRICEDATE")
        .drop_duplicates("PRICEDATE")
        .reset_index(drop=True)
    )

    if len(filtered) < 30:

        raise Exception(
            f"Not enough historical records for forecasting. "
            f"Found {len(filtered)} rows."
        )

    return filtered


# ============================================================
# Target-only XGBoost Forecast
# ============================================================

def train_and_forecast_flat_file(
    df,
    dcsid,
    mic,
    pricetype=None,
    horizon=30
):

    data = prepare_target_data(
        df,
        dcsid,
        mic,
        pricetype
    )

    # --------------------------------------------------------
    # Create target lag features
    # --------------------------------------------------------

    data["TARGET_LAG_1"] = data["PRICE"].shift(1)
    data["TARGET_LAG_7"] = data["PRICE"].shift(7)

   # data = data.dropna().reset_index(drop=True)
    data = data.dropna(
    subset=[
        "PRICE",
        "TARGET_LAG_1",
        "TARGET_LAG_7"
    ]
).reset_index(drop=True)

    # --------------------------------------------------------
    # Features
    # --------------------------------------------------------

    features = [
        "TARGET_LAG_1",
        "TARGET_LAG_7"
    ]

    X = data[features]
    y = data["PRICE"]

    # --------------------------------------------------------
    # XGBoost
    # --------------------------------------------------------

    model = XGBRegressor(
        n_estimators=80,
        max_depth=3,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=1.0,
        objective="reg:squarederror",
        random_state=42,
        n_jobs=1,
        tree_method="hist"
    )

    model.fit(X, y)

    # --------------------------------------------------------
    # Historical values
    # --------------------------------------------------------

    history = data["PRICE"].tolist()

    last_date = data["PRICEDATE"].iloc[-1]

    forecasts = []

    # --------------------------------------------------------
    # Recursive forecasting
    # --------------------------------------------------------

    for i in range(horizon):

        target_lag_1 = history[-1]
        target_lag_7 = history[-7]

        X_future = pd.DataFrame(
            [[
                target_lag_1,
                target_lag_7
            ]],
            columns=features
        )

        prediction = model.predict(X_future)[0]

        prediction = float(prediction)

        history.append(prediction)

        forecast_date = (
            last_date +
            pd.Timedelta(days=i + 1)
        )

        forecasts.append({
            "date": forecast_date.strftime("%Y-%m-%d"),
            "predicted_value": round(prediction, 4)
        })

    return forecasts