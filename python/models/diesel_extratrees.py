#Diesel ExtraTrees design uses recursive forecasting for both WTI and Brent
import numpy as np
import pandas as pd

from sklearn.ensemble import ExtraTreesRegressor


# ============================================================
# Configuration
# ============================================================

LAGS = [1, 2, 3, 4, 8]

RANDOM_STATE = 42


# ============================================================
# Generic recursive ExtraTrees model
# ============================================================

def train_factor_model(
    series,
    value_column,
    n_estimators=300,
    max_depth=12
):

    data = series.copy()

    data["DATE"] = pd.to_datetime(
        data["DATE"],
        errors="coerce"
    )

    data[value_column] = pd.to_numeric(
        data[value_column],
        errors="coerce"
    )

    data = (
        data
        .dropna(subset=["DATE", value_column])
        .sort_values("DATE")
        .drop_duplicates("DATE")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # Create lag features
    # --------------------------------------------------------

    for lag in LAGS:

        data[f"LAG_{lag}"] = (
            data[value_column].shift(lag)
        )

    feature_columns = [
        f"LAG_{lag}"
        for lag in LAGS
    ]

    data = data.dropna(
        subset=feature_columns + [value_column]
    ).copy()

    if len(data) < 30:

        raise ValueError(
            f"Not enough history for {value_column}. "
            f"Only {len(data)} usable rows."
        )

    model = ExtraTreesRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        min_samples_leaf=2,
        random_state=RANDOM_STATE,
        n_jobs=-1
    )

    model.fit(
        data[feature_columns],
        data[value_column]
    )

    return model, feature_columns


def recursive_factor_forecast(
    history,
    value_column,
    horizon
):

    history = history.copy()

    history["DATE"] = pd.to_datetime(
        history["DATE"],
        errors="coerce"
    )

    history[value_column] = pd.to_numeric(
        history[value_column],
        errors="coerce"
    )

    history = (
        history
        .dropna(subset=["DATE", value_column])
        .sort_values("DATE")
        .drop_duplicates("DATE")
        .reset_index(drop=True)
    )

    model, feature_columns = train_factor_model(
        history,
        value_column
    )

    values = (
        history[value_column]
        .astype(float)
        .tolist()
    )

    last_date = history["DATE"].iloc[-1]

    forecasts = []

    for step in range(1, horizon + 1):

        features = {}

        for lag in LAGS:

            features[
                f"LAG_{lag}"
            ] = values[-lag]

        X_future = pd.DataFrame(
            [features],
            columns=feature_columns
        )

        prediction = float(
            model.predict(X_future)[0]
        )

        values.append(prediction)

        forecast_date = (
            last_date
            + pd.Timedelta(days=7 * step)
        )

        forecasts.append(
            {
                "DATE": forecast_date,
                "VALUE": prediction
            }
        )

    return pd.DataFrame(forecasts)


# ============================================================
# Convert daily WTI / Brent to Monday weekly data
# ============================================================

def prepare_weekly_factor(
    df,
    date_column="DATE",
    value_column="VALUE"
):

    data = df.copy()

    data[date_column] = pd.to_datetime(
        data[date_column],
        errors="coerce"
    )

    data[value_column] = pd.to_numeric(
        data[value_column],
        errors="coerce"
    )

    data = data.dropna(
        subset=[
            date_column,
            value_column
        ]
    ).copy()

    data = (
        data[
            [
                date_column,
                value_column
            ]
        ]
        .sort_values(date_column)
        .set_index(date_column)
        .resample("W-MON")
        .last()
        .dropna()
        .reset_index()
    )

    data.columns = [
        "DATE",
        "VALUE"
    ]

    return data


# ============================================================
# Prepare Diesel weekly target
# ============================================================

def prepare_diesel_target(
    df,
    dcsid,
    mic,
    pricetype=None
):

    data = df.copy()

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

    selected = data[
        (data["DCSID"] == str(dcsid).strip())
        &
        (data["MIC"] == str(mic).strip())
    ].copy()

    if pricetype is not None:

        selected = selected[
            selected["PRICETYPE"]
            .astype(str)
            .str.strip()
            ==
            str(pricetype).strip()
        ].copy()

    if selected.empty:

        raise ValueError(
            f"No Diesel data found for "
            f"DCSID={dcsid}, MIC={mic}"
        )

    # --------------------------------------------------------
    # IMPORTANT:
    # CSV dates are DD-MM-YYYY.
    # --------------------------------------------------------

    selected["PRICEDATE"] = pd.to_datetime(
        selected["PRICEDATE"],
        format="%d-%m-%Y",
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
    ).copy()

    selected = (
        selected[
            [
                "PRICEDATE",
                "PRICE"
            ]
        ]
        .groupby(
            "PRICEDATE",
            as_index=False
        )
        .mean()
        .sort_values("PRICEDATE")
    )

    if len(selected) < 30:

        raise ValueError(
            f"Not enough Diesel history. "
            f"Only {len(selected)} rows available."
        )

    selected = selected.rename(
        columns={
            "PRICEDATE": "DATE",
            "PRICE": "DIESEL"
        }
    )

    return selected


# ============================================================
# Build Diesel training dataset
# ============================================================

def build_training_data(
    diesel,
    wti,
    brent
):

    # --------------------------------------------------------
    # Prepare WTI / Brent as weekly Monday data
    # --------------------------------------------------------

    wti_weekly = prepare_weekly_factor(
        wti
    )

    brent_weekly = prepare_weekly_factor(
        brent
    )

    # --------------------------------------------------------
    # Merge with Diesel weekly dates
    # --------------------------------------------------------

    data = diesel.merge(
        wti_weekly.rename(
            columns={
                "VALUE": "WTI"
            }
        ),
        on="DATE",
        how="left"
    )

    data = data.merge(
        brent_weekly.rename(
            columns={
                "VALUE": "BRENT"
            }
        ),
        on="DATE",
        how="left"
    )

    data = data.sort_values(
        "DATE"
    ).copy()

    # --------------------------------------------------------
    # Fill missing factor observations
    # --------------------------------------------------------

    data["WTI"] = data["WTI"].ffill()
    data["BRENT"] = data["BRENT"].ffill()

    data = data.dropna(
        subset=[
            "DIESEL",
            "WTI",
            "BRENT"
        ]
    ).copy()

    # --------------------------------------------------------
    # Diesel lags
    # --------------------------------------------------------

    for lag in LAGS:

        data[
            f"DIESEL_LAG_{lag}"
        ] = data["DIESEL"].shift(lag)

    # --------------------------------------------------------
    # WTI / Brent lags
    # --------------------------------------------------------

    for lag in LAGS:

        data[
            f"WTI_LAG_{lag}"
        ] = data["WTI"].shift(lag)

        data[
            f"BRENT_LAG_{lag}"
        ] = data["BRENT"].shift(lag)

    feature_columns = []

    for lag in LAGS:
        feature_columns.append(
            f"DIESEL_LAG_{lag}"
        )

    for lag in LAGS:
        feature_columns.append(
            f"WTI_LAG_{lag}"
        )

    for lag in LAGS:
        feature_columns.append(
            f"BRENT_LAG_{lag}"
        )

    data = data.dropna(
        subset=feature_columns + ["DIESEL"]
    ).copy()

    if data.empty:

        raise ValueError(
            "No usable Diesel training rows after "
            "feature creation."
        )

    return data, feature_columns


# ============================================================
# Main Diesel forecast
# ============================================================

def train_and_forecast_diesel(
    df,
    wti_history,
    brent_history,
    dcsid,
    mic,
    pricetype=None,
    horizon=4,
    forecast_start_date=None
):

    # --------------------------------------------------------
    # Prepare Diesel target
    # --------------------------------------------------------

    diesel = prepare_diesel_target(
        df=df,
        dcsid=dcsid,
        mic=mic,
        pricetype=pricetype
    )

    # --------------------------------------------------------
    # Keep only history before forecast start
    # --------------------------------------------------------

    if forecast_start_date is not None:

        start = pd.Timestamp(
            forecast_start_date
        ).normalize()

        diesel = diesel[
            diesel["DATE"] < start
        ].copy()

    if diesel.empty:

        raise ValueError(
            "No Diesel history exists before "
            "forecast start date."
        )

    # --------------------------------------------------------
    # Prepare WTI and Brent weekly histories
    # --------------------------------------------------------

    if wti_history is None or wti_history.empty:

        raise ValueError(
            "WTI history is required."
        )

    if brent_history is None or brent_history.empty:

        raise ValueError(
            "Brent history is required."
        )

    wti_weekly = prepare_weekly_factor(
        wti_history
    )

    brent_weekly = prepare_weekly_factor(
        brent_history
    )

    # --------------------------------------------------------
    # Train separate recursive factor models
    # --------------------------------------------------------

    wti_future = recursive_factor_forecast(
        wti_weekly,
        "VALUE",
        horizon
    )

    brent_future = recursive_factor_forecast(
        brent_weekly,
        "VALUE",
        horizon
    )

    # --------------------------------------------------------
    # Build historical Diesel training set
    # --------------------------------------------------------

    training_data, feature_columns = (
        build_training_data(
            diesel=diesel,
            wti=wti_history,
            brent=brent_history
        )
    )

    X = training_data[
        feature_columns
    ]

    y = training_data[
        "DIESEL"
    ]

    # --------------------------------------------------------
    # Train Diesel ExtraTrees
    # --------------------------------------------------------

    diesel_model = ExtraTreesRegressor(
        n_estimators=400,
        max_depth=14,
        min_samples_leaf=2,
        random_state=RANDOM_STATE,
        n_jobs=-1
    )

    diesel_model.fit(
        X,
        y
    )

    # --------------------------------------------------------
    # Recursive Diesel forecast
    # --------------------------------------------------------

    history = training_data[
        [
            "DATE",
            "DIESEL",
            "WTI",
            "BRENT"
        ]
    ].copy()

    history = history.sort_values(
        "DATE"
    ).reset_index(drop=True)

    diesel_values = (
        history["DIESEL"]
        .astype(float)
        .tolist()
    )

    wti_values = (
        history["WTI"]
        .astype(float)
        .tolist()
    )

    brent_values = (
        history["BRENT"]
        .astype(float)
        .tolist()
    )

    forecasts = []

    # --------------------------------------------------------
    # Generate exact weekly forecast dates
    # --------------------------------------------------------

    if forecast_start_date is not None:

        first_date = pd.Timestamp(
            forecast_start_date
        ).normalize()

        # Diesel data is Monday-based.
        # Move to Monday if JOBSTARTDATE is not Monday.
        first_date = (
            first_date
            + pd.Timedelta(
                days=(-first_date.weekday()) % 7
            )
        )

    else:

        first_date = (
            history["DATE"].iloc[-1]
            + pd.Timedelta(days=7)
        )

    for i in range(horizon):

        forecast_date = (
            first_date
            + pd.Timedelta(days=7 * i)
        )

        # ----------------------------------------------------
        # Future factor values from recursive factor forecasts
        # ----------------------------------------------------

        future_wti = float(
            wti_future.iloc[i]["VALUE"]
        )

        future_brent = float(
            brent_future.iloc[i]["VALUE"]
        )

        wti_values.append(
            future_wti
        )

        brent_values.append(
            future_brent
        )

        # ----------------------------------------------------
        # Build Diesel feature vector
        # ----------------------------------------------------

        features = {}

        for lag in LAGS:

            features[
                f"DIESEL_LAG_{lag}"
            ] = diesel_values[-lag]

        for lag in LAGS:

            features[
                f"WTI_LAG_{lag}"
            ] = wti_values[-lag]

            features[
                f"BRENT_LAG_{lag}"
            ] = brent_values[-lag]

        X_future = pd.DataFrame(
            [features],
            columns=feature_columns
        )

        prediction = float(
            diesel_model.predict(
                X_future
            )[0]
        )

        # ----------------------------------------------------
        # Add prediction to recursive history
        # ----------------------------------------------------

        diesel_values.append(
            prediction
        )

        forecasts.append(
            {
                "date": forecast_date.strftime(
                    "%Y-%m-%d"
                ),
                "predicted_value": prediction
            }
        )

    return forecasts