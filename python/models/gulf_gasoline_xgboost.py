import os
import json
import pandas as pd

from hdbcli import dbapi
from xgboost import XGBRegressor


# ============================================================
# Configuration
# ============================================================

HANA_SERVICE_NAME = "Forecast_model_cap-db"

TARGET_TABLE = '"FORECAST_DATA_EIA_GULF_GASOLINE_PRICES"'
WTI_TABLE = '"FORECAST_DATA_EIA_WTI_PRICES"'


# ============================================================
# Read VCAP_SERVICES
# ============================================================

def get_vcap_services():

    vcap_services = os.getenv("VCAP_SERVICES")

    if not vcap_services:
        raise Exception("VCAP_SERVICES not found")

    return json.loads(vcap_services)


# ============================================================
# Get HANA credentials
# ============================================================

def get_hana_credentials():

    services = get_vcap_services()

    # Try exact service name first
    for service_type, service_list in services.items():

        for service in service_list:

            if service.get("name") == HANA_SERVICE_NAME:

                credentials = service.get("credentials")

                if credentials:
                    return credentials

    # Fallback - find HANA credentials
    for service_type, service_list in services.items():

        for service in service_list:

            credentials = service.get("credentials", {})

            if (
                credentials.get("host")
                and credentials.get("port")
                and credentials.get("user")
                and credentials.get("password")
                and credentials.get("schema")
            ):
                return credentials

    raise Exception("HANA credentials not found")


# ============================================================
# Connect to HANA
# ============================================================

def get_hana_connection():

    credentials = get_hana_credentials()

    connection = dbapi.connect(
        address=credentials["host"],
        port=int(credentials["port"]),
        user=credentials["user"],
        password=credentials["password"],
        encrypt=True
    )

    cursor = connection.cursor()

    cursor.execute(
        f'SET SCHEMA "{credentials["schema"]}"'
    )

    cursor.close()

    return connection


# ============================================================
# Load data from HANA
# ============================================================

def get_model_data():

    connection = get_hana_connection()

    try:

        target_sql = f"""
            SELECT
                "DATE",
                "VALUE"
            FROM {TARGET_TABLE}
            ORDER BY "DATE"
        """

        wti_sql = f"""
            SELECT
                "DATE",
                "VALUE"
            FROM {WTI_TABLE}
            ORDER BY "DATE"
        """

        cursor = connection.cursor()

        # ----------------------------------------------------
        # Gulf Gasoline
        # ----------------------------------------------------

        cursor.execute(target_sql)

        target_rows = cursor.fetchall()

        target = pd.DataFrame(
            target_rows,
            columns=["DATE", "TARGET"]
        )

        # ----------------------------------------------------
        # WTI
        # ----------------------------------------------------

        cursor.execute(wti_sql)

        wti_rows = cursor.fetchall()

        wti = pd.DataFrame(
            wti_rows,
            columns=["DATE", "WTI"]
        )

    finally:

        connection.close()

    # --------------------------------------------------------
    # Convert types
    # --------------------------------------------------------

    target["DATE"] = pd.to_datetime(
        target["DATE"],
        errors="coerce"
    )

    wti["DATE"] = pd.to_datetime(
        wti["DATE"],
        errors="coerce"
    )

    target["TARGET"] = pd.to_numeric(
        target["TARGET"],
        errors="coerce"
    )

    wti["WTI"] = pd.to_numeric(
        wti["WTI"],
        errors="coerce"
    )

    # Remove bad records
    target = target.dropna()
    wti = wti.dropna()

    # --------------------------------------------------------
    # Merge
    # --------------------------------------------------------

    df = pd.merge(
        target,
        wti,
        on="DATE",
        how="inner"
    )

    df = (
        df
        .sort_values("DATE")
        .drop_duplicates("DATE")
        .reset_index(drop=True)
    )

    if df.empty:
        raise Exception(
            "No overlapping Gulf Gasoline and WTI data found"
        )

    return df


# ============================================================
# Train XGBoost and Forecast
# ============================================================

def train_and_forecast(horizon=30):

    df = get_model_data()

    if len(df) < 100:

        raise Exception(
            f"Not enough aligned data. "
            f"Found {len(df)} rows."
        )

    # --------------------------------------------------------
    # Create lag features
    # --------------------------------------------------------

    df["TARGET_LAG_1"] = df["TARGET"].shift(1)
    df["TARGET_LAG_7"] = df["TARGET"].shift(7)

    df["WTI_LAG_1"] = df["WTI"].shift(1)
    df["WTI_LAG_7"] = df["WTI"].shift(7)

    df = df.dropna().reset_index(drop=True)

    # --------------------------------------------------------
    # Features
    # --------------------------------------------------------

    features = [
        "TARGET_LAG_1",
        "TARGET_LAG_7",
        "WTI_LAG_1",
        "WTI_LAG_7"
    ]

    X = df[features]
    y = df["TARGET"]

    # --------------------------------------------------------
    # Lightweight XGBoost
    # --------------------------------------------------------

    model = XGBRegressor(
        n_estimators=80,
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

    # --------------------------------------------------------
    # Historical values
    # --------------------------------------------------------

    history_target = df["TARGET"].tolist()
    history_wti = df["WTI"].tolist()

    last_date = df["DATE"].iloc[-1]

    forecasts = []

    # Last available WTI
    # Future WTI is currently unavailable,
    # so use the latest known WTI value.
    future_wti = history_wti[-1]

    # --------------------------------------------------------
    # Recursive forecasting
    # --------------------------------------------------------

    for i in range(horizon):

        target_lag_1 = history_target[-1]
        target_lag_7 = history_target[-7]

        wti_lag_1 = (
            history_wti[-1]
            if len(history_wti) >= 1
            else future_wti
        )

        wti_lag_7 = (
            history_wti[-7]
            if len(history_wti) >= 7
            else future_wti
        )

        X_future = pd.DataFrame(
            [[
                target_lag_1,
                target_lag_7,
                wti_lag_1,
                wti_lag_7
            ]],
            columns=features
        )

        prediction = model.predict(X_future)[0]

        prediction = float(prediction)

        # Add predicted gasoline value
        history_target.append(prediction)

        # WTI remains at latest known value
        history_wti.append(future_wti)

        forecast_date = (
            last_date +
            pd.Timedelta(days=i + 1)
        )

        forecasts.append({
            "date": forecast_date.strftime("%Y-%m-%d"),
            "predicted_value": round(prediction, 4)
        })

    return forecasts