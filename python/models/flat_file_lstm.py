import numpy as np
import pandas as pd

from sklearn.preprocessing import MinMaxScaler
from tensorflow.keras.models import Sequential
from tensorflow.keras.layers import LSTM, Dense, Dropout
from tensorflow.keras.callbacks import EarlyStopping


# ============================================================
# Helper: create univariate sequences
# ============================================================

def create_univariate_sequences(
    values,
    sequence_length
):
    X = []
    y = []

    for i in range(
        sequence_length,
        len(values)
    ):
        X.append(
            values[
                i - sequence_length:i
            ]
        )

        y.append(
            values[i]
        )

    return (
        np.array(X),
        np.array(y)
    )


# ============================================================
# Forecast future WTI using univariate LSTM
# ============================================================

def forecast_wti_lstm(
    wti_history,
    horizon,
    sequence_length=30,
    epochs=30,
    batch_size=16
):
    """
    Forecast future WTI recursively using a
    univariate LSTM.

    Historical WTI
        ->
    WTI LSTM
        ->
    Future WTI values
    """

    if (
        wti_history is None
        or wti_history.empty
    ):
        raise ValueError(
            "WTI history is empty"
        )

    wti = wti_history.copy()

    # --------------------------------------------------------
    # Normalize WTI fields
    # --------------------------------------------------------

    wti["DATE"] = pd.to_datetime(
        wti["DATE"],
        errors="coerce"
    )

    wti["VALUE"] = pd.to_numeric(
        wti["VALUE"],
        errors="coerce"
    )

    wti = (
        wti
        .dropna(
            subset=["DATE", "VALUE"]
        )
        .sort_values("DATE")
        .drop_duplicates("DATE")
        .reset_index(drop=True)
    )

    if len(wti) <= sequence_length:
        raise ValueError(
            "Not enough WTI history for LSTM. "
            f"Need more than {sequence_length} "
            f"observations, found {len(wti)}."
        )

    # --------------------------------------------------------
    # Scale WTI
    # --------------------------------------------------------

    values = (
        wti["VALUE"]
        .values
        .reshape(-1, 1)
    )

    scaler = MinMaxScaler()

    scaled_values = scaler.fit_transform(
        values
    )

    # --------------------------------------------------------
    # Create sequences
    # --------------------------------------------------------

    X, y = create_univariate_sequences(
        scaled_values,
        sequence_length
    )

    if len(X) == 0:
        raise ValueError(
            "Unable to create WTI LSTM sequences"
        )

    X = X.reshape(
        X.shape[0],
        X.shape[1],
        1
    )

    # --------------------------------------------------------
    # Build WTI LSTM
    # --------------------------------------------------------

    model = Sequential([
        LSTM(
            64,
            return_sequences=True,
            input_shape=(
                sequence_length,
                1
            )
        ),

        Dropout(0.2),

        LSTM(32),

        Dropout(0.2),

        Dense(1)
    ])

    model.compile(
        optimizer="adam",
        loss="mse"
    )

    early_stopping = EarlyStopping(
        monitor="loss",
        patience=5,
        restore_best_weights=True
    )

    model.fit(
        X,
        y,
        epochs=epochs,
        batch_size=batch_size,
        verbose=0,
        callbacks=[early_stopping]
    )

    # --------------------------------------------------------
    # Recursive WTI forecasting
    # --------------------------------------------------------

    history = (
        scaled_values
        .flatten()
        .tolist()
    )

    future_values = []

    for _ in range(horizon):

        input_sequence = np.array(
            history[-sequence_length:]
        ).reshape(
            1,
            sequence_length,
            1
        )

        prediction = model.predict(
            input_sequence,
            verbose=0
        )[0][0]

        prediction = float(
            prediction
        )

        history.append(
            prediction
        )

        future_values.append(
            prediction
        )

    # --------------------------------------------------------
    # Convert WTI back to original scale
    # --------------------------------------------------------

    future_values = np.array(
        future_values
    ).reshape(-1, 1)

    future_values = scaler.inverse_transform(
        future_values
    ).flatten()

    return future_values


# ============================================================
# Main LSTM forecast
# ============================================================

def train_and_forecast_flat_file_lstm(
    df,
    wti_history,
    dcsid,
    mic,
    pricetype,
    horizon,
    forecast_start_date
):
    """
    Gulf Gasoline LSTM forecast.

    Architecture:

        Historical WTI
              |
              v
        WTI LSTM
              |
              v
        Future WTI
              |
              v
    Historical Gulf Gasoline
              +
        Future WTI
              |
              v
       Gulf Gasoline LSTM
              |
              v
     Recursive forecast

    Features:

        TARGET_LAG_1
        TARGET_LAG_7
        WTI_LAG_1
        WTI_LAG_7
    """

    # ========================================================
    # 1. Validate inputs
    # ========================================================

    if df is None or df.empty:
        raise ValueError(
            "Flat-file data is empty"
        )

    if (
        wti_history is None
        or wti_history.empty
    ):
        raise ValueError(
            "WTI history is empty"
        )

    if not dcsid:
        raise ValueError(
            "DCSID is required for LSTM"
        )

    if not mic:
        raise ValueError(
            "MIC is required for LSTM"
        )

    if not pricetype:
        raise ValueError(
            "PRICETYPE is required for LSTM"
        )

    if horizon <= 0:
        raise ValueError(
            "LSTM horizon must be positive"
        )

    # ========================================================
    # 2. Copy target data
    # ========================================================

    data = df.copy()

    # ========================================================
    # 3. Normalize fields
    # ========================================================

    data["DCSID"] = (
        data["DCSID"]
        .astype(str)
        .str.strip()
    )

    data["MIC"] = (
        data["MIC"]
        .astype(str)
        .str.strip()
    )

    data["PRICETYPE"] = (
        data["PRICETYPE"]
        .astype(str)
        .str.strip()
    )

    # ========================================================
    # 4. Select target instrument
    # ========================================================

    selected = data[
        (data["DCSID"] == str(dcsid).strip())
        &
        (data["MIC"] == str(mic).strip())
        &
        (
            data["PRICETYPE"]
            == str(pricetype).strip()
        )
    ].copy()

    if selected.empty:
        raise ValueError(
            f"No flat-file data found for "
            f"{dcsid} / {mic} / {pricetype}"
        )

    # ========================================================
    # 5. Prepare target dates and prices
    # ========================================================

    selected["PRICEDATE"] = pd.to_datetime(
        selected["PRICEDATE"],
        errors="coerce"
    )

    selected["PRICE"] = pd.to_numeric(
        selected["PRICE"],
        errors="coerce"
    )

    selected = selected.dropna(
        subset=[
            "PRICEDATE",
            "PRICE"
        ]
    )

    if selected.empty:
        raise ValueError(
            "No valid PRICEDATE / PRICE "
            "observations available for LSTM"
        )

    # ========================================================
    # 6. Forecast start date
    # ========================================================

    forecast_start = pd.Timestamp(
        forecast_start_date
    ).normalize()

    if pd.isna(forecast_start):
        raise ValueError(
            "Invalid LSTM forecast start date"
        )

    # Only historical target observations
    selected = selected[
        selected["PRICEDATE"]
        < forecast_start
    ].copy()

    if selected.empty:
        raise ValueError(
            "No historical observations exist "
            "before the LSTM forecast start date"
        )

    # ========================================================
    # 7. Prepare WTI
    # ========================================================

    wti = wti_history.copy()

    wti["DATE"] = pd.to_datetime(
        wti["DATE"],
        errors="coerce"
    )

    wti["VALUE"] = pd.to_numeric(
        wti["VALUE"],
        errors="coerce"
    )

    wti = (
        wti
        .dropna(
            subset=[
                "DATE",
                "VALUE"
            ]
        )
        .sort_values("DATE")
        .drop_duplicates("DATE")
        .reset_index(drop=True)
    )

    if wti.empty:
        raise ValueError(
            "No valid WTI history available for LSTM"
        )

    # Only WTI available before forecast
    wti_training = wti[
        wti["DATE"] < forecast_start
    ].copy()

    if wti_training.empty:
        raise ValueError(
            "No WTI history exists before "
            "the LSTM forecast start date"
        )

    # ========================================================
    # 8. Rename target fields
    # ========================================================

    selected = selected[
        [
            "PRICEDATE",
            "PRICE"
        ]
    ].copy()

    selected = selected.rename(
        columns={
            "PRICEDATE": "DATE",
            "PRICE": "TARGET"
        }
    )

    wti_training = wti_training[
        [
            "DATE",
            "VALUE"
        ]
    ].rename(
        columns={
            "VALUE": "WTI"
        }
    )

    # ========================================================
    # 9. Align Gulf Gasoline and WTI
    # ========================================================

    merged = pd.merge(
        selected,
        wti_training,
        on="DATE",
        how="inner"
    )

    merged = (
        merged
        .sort_values("DATE")
        .drop_duplicates("DATE")
        .reset_index(drop=True)
    )

    if len(merged) < 60:
        raise ValueError(
            "Insufficient overlapping Gulf Gasoline "
            "and WTI history for LSTM. "
            f"Found {len(merged)} observations."
        )

    # ========================================================
    # 10. Create lag features
    # ========================================================

    merged["TARGET_LAG_1"] = (
        merged["TARGET"].shift(1)
    )

    merged["TARGET_LAG_7"] = (
        merged["TARGET"].shift(7)
    )

    merged["WTI_LAG_1"] = (
        merged["WTI"].shift(1)
    )

    merged["WTI_LAG_7"] = (
        merged["WTI"].shift(7)
    )

    merged = (
        merged
        .dropna()
        .reset_index(drop=True)
    )

    if len(merged) < 40:
        raise ValueError(
            "Insufficient data after creating "
            "LSTM lag features"
        )

    # ========================================================
    # 11. Define features
    # ========================================================

    features = [
        "TARGET_LAG_1",
        "TARGET_LAG_7",
        "WTI_LAG_1",
        "WTI_LAG_7"
    ]

    # ========================================================
    # 12. Create scalers
    # ========================================================

    target_scaler = MinMaxScaler()

    wti_scaler = MinMaxScaler()

    target_scaler.fit(
        merged[["TARGET"]]
    )

    wti_scaler.fit(
        merged[["WTI"]]
    )

    # ========================================================
    # 13. Scale model data
    # ========================================================

    model_data = merged.copy()

    model_data["TARGET"] = (
        target_scaler
        .transform(
            model_data[["TARGET"]]
        )
        .flatten()
    )

    model_data["WTI"] = (
        wti_scaler
        .transform(
            model_data[["WTI"]]
        )
        .flatten()
    )

    model_data["TARGET_LAG_1"] = (
        target_scaler
        .transform(
            model_data[
                ["TARGET_LAG_1"]
            ]
        )
        .flatten()
    )

    model_data["TARGET_LAG_7"] = (
        target_scaler
        .transform(
            model_data[
                ["TARGET_LAG_7"]
            ]
        )
        .flatten()
    )

    model_data["WTI_LAG_1"] = (
        wti_scaler
        .transform(
            model_data[
                ["WTI_LAG_1"]
            ]
        )
        .flatten()
    )

    model_data["WTI_LAG_7"] = (
        wti_scaler
        .transform(
            model_data[
                ["WTI_LAG_7"]
            ]
        )
        .flatten()
    )

    # ========================================================
    # 14. Build Gulf Gasoline sequences
    # ========================================================

    sequence_length = 30

    feature_matrix = (
        model_data[
            features
        ].values
    )

    target_vector = (
        model_data[
            "TARGET"
        ].values
    )

    X = []
    y = []

    for i in range(
        sequence_length,
        len(model_data)
    ):
        X.append(
            feature_matrix[
                i - sequence_length:i
            ]
        )

        y.append(
            target_vector[i]
        )

    X = np.array(X)
    y = np.array(y)

    if len(X) == 0:
        raise ValueError(
            "Unable to create Gulf Gasoline "
            "LSTM training sequences"
        )

    # ========================================================
    # 15. Build Gulf Gasoline LSTM
    # ========================================================

    model = Sequential([
        LSTM(
            64,
            return_sequences=True,
            input_shape=(
                sequence_length,
                len(features)
            )
        ),

        Dropout(0.2),

        LSTM(32),

        Dropout(0.2),

        Dense(1)
    ])

    model.compile(
        optimizer="adam",
        loss="mse"
    )

    early_stopping = EarlyStopping(
        monitor="loss",
        patience=5,
        restore_best_weights=True
    )

    model.fit(
        X,
        y,
        epochs=40,
        batch_size=16,
        verbose=0,
        callbacks=[early_stopping]
    )

    # ========================================================
    # 16. Forecast future WTI
    # ========================================================

    future_wti = forecast_wti_lstm(
        wti_history=wti_training,
        horizon=horizon,
        sequence_length=30,
        epochs=30,
        batch_size=16
    )

    # ========================================================
    # 17. Prepare recursive histories
    # ========================================================

    history_target = (
        merged["TARGET"]
        .tolist()
    )

    history_wti = (
        merged["WTI"]
        .tolist()
    )

    # Start with complete historical
    # feature sequence
    recursive_features = (
        model_data[
            features
        ]
        .values
        .tolist()
    )

    forecasts = []

    # ========================================================
    # 18. Recursive Gulf Gasoline forecasting
    # ========================================================

    for i in range(horizon):

        # ----------------------------------------------------
        # Future WTI predicted by WTI LSTM
        # ----------------------------------------------------

        future_wti_value = float(
            future_wti[i]
        )

        history_wti.append(
            future_wti_value
        )

        # ----------------------------------------------------
        # Target lags
        # ----------------------------------------------------

        target_lag_1 = (
            history_target[-1]
        )

        target_lag_7 = (
            history_target[-7]
        )

        # ----------------------------------------------------
        # WTI lags
        # ----------------------------------------------------

        wti_lag_1 = (
            history_wti[-1]
        )

        wti_lag_7 = (
            history_wti[-7]
        )

        # ----------------------------------------------------
        # Scale current feature row
        # ----------------------------------------------------

        scaled_target_lag_1 = (
            target_scaler
            .transform(
                [[target_lag_1]]
            )[0][0]
        )

        scaled_target_lag_7 = (
            target_scaler
            .transform(
                [[target_lag_7]]
            )[0][0]
        )

        scaled_wti_lag_1 = (
            wti_scaler
            .transform(
                [[wti_lag_1]]
            )[0][0]
        )

        scaled_wti_lag_7 = (
            wti_scaler
            .transform(
                [[wti_lag_7]]
            )[0][0]
        )

        current_features = [
            scaled_target_lag_1,
            scaled_target_lag_7,
            scaled_wti_lag_1,
            scaled_wti_lag_7
        ]

        # ----------------------------------------------------
        # Add current row to recursive history
        # ----------------------------------------------------

        recursive_features.append(
            current_features
        )

        # ----------------------------------------------------
        # Take last 30 observations
        # ----------------------------------------------------

        sequence = np.array(
            recursive_features[
                -sequence_length:
            ]
        )

        sequence = sequence.reshape(
            1,
            sequence_length,
            len(features)
        )

        # ----------------------------------------------------
        # Predict Gulf Gasoline
        # ----------------------------------------------------

        scaled_prediction = (
            model.predict(
                sequence,
                verbose=0
            )[0][0]
        )

        # ----------------------------------------------------
        # Convert prediction to original scale
        # ----------------------------------------------------

        prediction = (
            target_scaler
            .inverse_transform(
                [[scaled_prediction]]
            )[0][0]
        )

        prediction = float(
            prediction
        )

        # ----------------------------------------------------
        # Add predicted Gulf Gasoline to history
        # ----------------------------------------------------

        history_target.append(
            prediction
        )

        # ----------------------------------------------------
        # Forecast date
        # ----------------------------------------------------

        forecast_date = (
            forecast_start
            + pd.Timedelta(days=i)
        )

        forecasts.append({
            "date": forecast_date.strftime(
                "%Y-%m-%d"
            ),
            "predicted_value": round(
                prediction,
                4
            )
        })

    # ========================================================
    # 19. Final validation
    # ========================================================

    if len(forecasts) != horizon:
        raise ValueError(
            "LSTM did not produce the requested "
            f"horizon. Expected {horizon}, "
            f"got {len(forecasts)}."
        )

    for row in forecasts:

        if not np.isfinite(
            row["predicted_value"]
        ):
            raise ValueError(
                "LSTM produced a non-finite "
                "forecast value"
            )

    return forecasts