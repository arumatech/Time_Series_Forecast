import os
import json
import requests
import sklearn
import pandas as pd
import threading
import time
from datetime import date, datetime
from math import isfinite

from flask import Flask, jsonify, request
from hdbcli import dbapi

from models.gulf_gasoline_xgboost import train_and_forecast
from models.flat_file_xgboost import train_and_forecast_flat_file
from models.flat_file_hana_apl import train_and_forecast_hana_apl

# Optional API-factor model.
# Your existing app already uses this function for A3 + GG.
try:
    from models.flat_file_xgboost import train_and_forecast_flat_file_with_api
except ImportError:
    train_and_forecast_flat_file_with_api = None


app = Flask(__name__)


# ============================================================
# Configuration
# ============================================================

DESTINATION_NAME = "EIA_API"
FRED_DESTINATION_NAME = "FRED_API"

HANA_SERVICE_NAME = "TIME_SERIES_FORECAST-db"

TABLE_NAME = '"FORECAST_DATA_EIA_WTI_PRICES"'
FRED_TABLE_NAME = '"FORECAST_DATA_FRED_CURRENCY"'

FORECAST_RESULTS_TABLE = '"FORECAST_DATA_FORECAST_RESULTS"'
FORCORR_TABLE = '"ZRISK_FORCORR"'

JOBSUMM_TABLE = '"ZRISK_JOBSUMM"'

FLAT_FILE_TABLE = '"ZRISK_FLATFILE"'


# ============================================================
# Flat-file API-factor forecast configuration
# ============================================================

API_FORECAST_DCSID = "A3"
API_FORECAST_MIC = "GG"


# ============================================================
# Read VCAP_SERVICES
# ============================================================

def get_vcap_services():

    vcap_services = os.getenv("VCAP_SERVICES")

    if not vcap_services:
        raise Exception(
            "VCAP_SERVICES environment variable not found"
        )

    return json.loads(vcap_services)


# ============================================================
# Get HANA credentials
# ============================================================

def get_hana_credentials():

    services = get_vcap_services()

    # First: exact MTA service binding
    for service_type, service_list in services.items():

        for service in service_list:

            if service.get("name") == HANA_SERVICE_NAME:

                credentials = service.get("credentials")

                if credentials:
                    return credentials

    # Fallback: identify HANA service by credentials
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

    raise Exception(
        f"HANA service '{HANA_SERVICE_NAME}' not found "
        "in VCAP_SERVICES"
    )


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
# JOBSUMM - Get Pending Job
# ============================================================

def get_pending_job():

    connection = get_hana_connection()
    cursor = connection.cursor()

    sql = f"""
        SELECT
            "JOB_ID",
            "FORCORR",
            "MODEL",
            "SRCDCSID",
            "SRCMIC",
            "TARDCSID",
            "TARMIC",
            "HORTY",
            "HORVAL",
            "JOBSTARTDATE",
            "JOBSTATUS",
            "CREATEDBY"
        FROM {JOBSUMM_TABLE}
        WHERE "JOBSTATUS" = 'ENTERED'
            AND "FORCORR" = 'For'
        ORDER BY "JOB_ID"
        LIMIT 1
    """

    try:

        cursor.execute(sql)
        row = cursor.fetchone()

        if not row:
            return None

        return {
            "JOB_ID": row[0],
            "FORCORR": row[1],
            "MODEL": row[2],
            "SRCDCSID": row[3],
            "SRCMIC": row[4],
            "TARDCSID": row[5],
            "TARMIC": row[6],
            "HORTY": row[7],
            "HORVAL": row[8],
            "JOBSTARTDATE": row[9],
            "JOBSTATUS": row[10],
            "CREATEDBY": row[11]
        }

    finally:

        cursor.close()
        connection.close()


# ============================================================
# JOBSUMM - Update Job
# ============================================================

def update_job(
    job_id,
    status=None,
    completion=None,
    start_timestamp=False,
    end_timestamp=False
):

    connection = get_hana_connection()
    cursor = connection.cursor()

    updates = []
    values = []

    if status is not None:
        updates.append('"JOBSTATUS" = ?')
        values.append(status)

    if completion is not None:
        updates.append('"JOBCOMPPCT" = ?')
        values.append(completion)

    if start_timestamp:
        updates.append('"JOBSTTMSTMP" = CURRENT_TIMESTAMP')

    if end_timestamp:
        updates.append('"JOBENDTMSTMP" = CURRENT_TIMESTAMP')

    if not updates:
        cursor.close()
        connection.close()
        return

    sql = f"""
        UPDATE {JOBSUMM_TABLE}
        SET {", ".join(updates)}
        WHERE "JOB_ID" = ?
    """

    values.append(job_id)

    try:

        cursor.execute(sql, values)
        connection.commit()

    except Exception:

        connection.rollback()
        raise

    finally:

        cursor.close()
        connection.close()


# ============================================================
# Read Flat File Data from HANA
# ============================================================

def read_flat_file_from_hana():

    connection = get_hana_connection()
    cursor = connection.cursor()

    sql = f"""
        SELECT
            "DCSID",
            "MIC",
            "PRICETYPE",
            "MKEYDT",
            "PRICEDATE",
            "PRICE",
            "PER",
            "UOM",
            "CURRENCY",
            "FILENAME"
        FROM {FLAT_FILE_TABLE}
    """

    try:

        cursor.execute(sql)

        rows = cursor.fetchall()

        columns = [
            "DCSID",
            "MIC",
            "PRICETYPE",
            "MKEYDT",
            "PRICEDATE",
            "PRICE",
            "PER",
            "UOM",
            "CURRENCY",
            "FILENAME"
        ]

        return pd.DataFrame(
            rows,
            columns=columns
        )

    finally:

        cursor.close()
        connection.close()


# ============================================================
# Read WTI Data from HANA
# ============================================================

def read_wti_from_hana():

    connection = get_hana_connection()
    cursor = connection.cursor()

    sql = f"""
        SELECT
            "DATE",
            "VALUE"
        FROM {TABLE_NAME}
        ORDER BY "DATE"
    """

    try:

        cursor.execute(sql)

        rows = cursor.fetchall()

        return pd.DataFrame(
            rows,
            columns=[
                "DATE",
                "VALUE"
            ]
        )

    finally:

        cursor.close()
        connection.close()


# ============================================================
# JOBSUMM Worker
# ============================================================

def process_job(job):

    job_id = job["JOB_ID"]

    try:

        print(
            f"Starting JOB_ID: {job_id}"
        )

        # ----------------------------------------------------
        # 1. Mark job as RUNNING
        # ----------------------------------------------------

        update_job(
            job_id=job_id,
            status="RUNNING",
            completion=0,
            start_timestamp=True
        )

        if job.get("FORCORR") != "For":
            raise ValueError(
                "Only forecast jobs are supported"
            )

        model_name = str(
            job.get("MODEL") or ""
        ).strip()

        if model_name not in {
            "XGBoost",
            "HANA_APL"
        }:

            raise ValueError(
                f"Unsupported model '{model_name}'. "
                "Select XGBoost or HANA APL."
            )

        # ----------------------------------------------------
        # 2. Read flat-file data from HANA
        # ----------------------------------------------------

        update_job(
            job_id=job_id,
            completion=25
        )

        df = read_flat_file_from_hana()

        if df.empty:

            raise Exception(
                "ZRISK_FLATFILE contains no data"
            )

        # ----------------------------------------------------
        # Forecast start date
        # ----------------------------------------------------

        forecast_start_date = pd.to_datetime(
            job["JOBSTARTDATE"],
            errors="coerce"
        )

        if pd.isna(forecast_start_date):

            raise Exception(
                "JOBSTARTDATE is required and must be a valid date"
            )

        df["PRICEDATE"] = pd.to_datetime(
            df["PRICEDATE"],
            errors="coerce"
        )

        df = df[
            df["PRICEDATE"] < forecast_start_date
        ].copy()

        if df.empty:

            raise Exception(
                "No historical data exists before JOBSTARTDATE"
            )

        # ----------------------------------------------------
        # 3. Get job parameters
        # ----------------------------------------------------

        dcsid = job["TARDCSID"]
        mic = job["TARMIC"]

        horizon = int(job["HORVAL"])
        horizon_type = job["HORTY"]

        if horizon <= 0:
            raise ValueError(
                "HORVAL must be a positive integer"
            )

        if horizon_type not in {
            "D",
            "W",
            "M"
        }:
            raise ValueError(
                "HORTY must be D, W, or M"
            )

        if horizon_type == "W":
            horizon = horizon * 7

        elif horizon_type == "M":
            horizon = horizon * 30

        if not dcsid:
            raise Exception(
                "TARDCSID is required"
            )

        if not mic:
            raise Exception(
                "TARMIC is required"
            )

        if not horizon:
            raise Exception(
                "HORVAL is required"
            )

        # ----------------------------------------------------
        # Metadata row
        # ----------------------------------------------------

        metadata_date = (
            forecast_start_date
            - pd.Timedelta(days=1)
        )

        metadata_rows = df[
            (
                df["DCSID"]
                .astype(str)
                .str.strip()
                == str(dcsid).strip()
            )
            &
            (
                df["MIC"]
                .astype(str)
                .str.strip()
                == str(mic).strip()
            )
            &
            (
                df["PRICEDATE"]
                <= metadata_date
            )
        ]

        if not metadata_rows.empty:

            latest_metadata_date = (
                metadata_rows["PRICEDATE"].max()
            )

            metadata_rows = metadata_rows[
                metadata_rows["PRICEDATE"]
                == latest_metadata_date
            ]

        if metadata_rows.empty:

            raise Exception(
                "No flat-file row exists for the selected "
                "DCSID / MIC before JOBSTARTDATE"
            )

        if len(metadata_rows) > 1:

            raise Exception(
                "Multiple flat-file rows exist for the selected "
                "DCSID / MIC on the latest available date"
            )

        metadata_row = metadata_rows.iloc[0]

        # ----------------------------------------------------
        # 4. Run selected model
        # ----------------------------------------------------

        update_job(
            job_id=job_id,
            completion=50
        )

        print(
            f"JOB_ID {job_id}: "
            f"running {model_name} for "
            f"{dcsid} / {mic}"
        )

        # ====================================================
        # XGBOOST
        # ====================================================

        if model_name == "XGBoost":

            # ------------------------------------------------
            # A3 + GG
            # ------------------------------------------------
            # A3 + GG uses the WTI API-factor model.
            # Everything else uses the standard flat-file model.
            # ------------------------------------------------

            if (
                str(dcsid).strip()
                == API_FORECAST_DCSID
                and
                str(mic).strip()
                == API_FORECAST_MIC
            ):

                print(
                    f"JOB_ID {job_id}: "
                    f"using API-factor XGBoost "
                    f"for {dcsid} / {mic}"
                )

                if train_and_forecast_flat_file_with_api is None:

                    raise ImportError(
                        "train_and_forecast_flat_file_with_api "
                        "is not available in "
                        "models.flat_file_xgboost"
                    )

                # ------------------------------------------------
                # Read WTI API data already stored in HANA
                # ------------------------------------------------

                wti_df = read_wti_from_hana()

                if wti_df.empty:

                    raise Exception(
                        "FORECAST_DATA_EIA_WTI_PRICES "
                        "contains no data"
                    )

                # ------------------------------------------------
                # Run API-factor model
                # ------------------------------------------------

                forecasts = (
                    train_and_forecast_flat_file_with_api(
                        flat_df=df,
                        dcsid=dcsid,
                        mic=mic,
                        pricetype=None,
                        horizon=horizon,
                        api_factors=[
                            {
                                "df": wti_df,
                                "value_column": "VALUE",
                                "name": "WTI"
                            }
                        ]
                    )
                )

                # API-factor model may return dates based on
                # historical data, so align them to JOBSTARTDATE.
                for index, forecast in enumerate(
                    forecasts
                ):

                    forecast_date = (
                        forecast_start_date
                        + pd.Timedelta(days=index)
                    )

                    forecast["date"] = (
                        forecast_date.strftime(
                            "%Y-%m-%d"
                        )
                    )

            else:

                print(
                    f"JOB_ID {job_id}: "
                    f"using standard XGBoost "
                    f"for {dcsid} / {mic}"
                )

                forecasts = train_and_forecast_flat_file(
                    df=df,
                    dcsid=dcsid,
                    mic=mic,
                    pricetype=None,
                    horizon=horizon
                )

                # Preserve existing XGBoost behavior:
                # forecast begins on JOBSTARTDATE.
                for index, forecast in enumerate(
                    forecasts
                ):

                    forecast_date = (
                        forecast_start_date
                        + pd.Timedelta(days=index)
                    )

                    forecast["date"] = (
                        forecast_date.strftime(
                            "%Y-%m-%d"
                        )
                    )

        # ====================================================
        # HANA APL
        # ====================================================

        elif model_name == "HANA_APL":

            print(
                f"JOB_ID {job_id}: "
                f"using HANA APL "
                f"for {dcsid} / {mic}"
            )

            forecasts = train_and_forecast_hana_apl(
                df=df,
                dcsid=dcsid,
                mic=mic,
                credentials=get_hana_credentials(),
                pricetype=None,
                horizon=horizon,
                forecast_start_date=forecast_start_date
            )

        # ----------------------------------------------------
        # Validate entire forecast
        # ----------------------------------------------------

        expected_dates = (
            pd.date_range(
                forecast_start_date,
                periods=horizon,
                freq="D"
            )
            .strftime("%Y-%m-%d")
            .tolist()
        )

        if (
            not isinstance(
                forecasts,
                list
            )
            or len(forecasts) != horizon
        ):

            raise ValueError(
                f"{model_name} did not return "
                f"{horizon} forecast rows"
            )

        if [
            row["date"]
            for row in forecasts
        ] != expected_dates:

            raise ValueError(
                f"{model_name} returned "
                "unexpected forecast dates"
            )

        if not all(
            isfinite(
                float(
                    row["predicted_value"]
                )
            )
            for row in forecasts
        ):

            raise ValueError(
                f"{model_name} returned "
                "invalid forecast prices"
            )

        # ----------------------------------------------------
        # 5. Forecast generated
        # ----------------------------------------------------

        update_job(
            job_id=job_id,
            completion=75
        )

        # ----------------------------------------------------
        # 6. Save forecast results
        # ----------------------------------------------------

        save_result = save_job_forecast_results(
            job=job,
            forecasts=forecasts,
            metadata_row=metadata_row
        )

        print(
            f"JOB_ID {job_id}: "
            f"{save_result['records_saved']} "
            f"records saved"
        )

        # ----------------------------------------------------
        # 7. Mark job as COMPLETED
        # ----------------------------------------------------

        update_job(
            job_id=job_id,
            status="COMPLETED",
            completion=100,
            end_timestamp=True
        )

        print(
            f"JOB_ID {job_id} completed"
        )

    except Exception as e:

        print(
            f"JOB_ID {job_id} failed: {str(e)}"
        )

        try:

            update_job(
                job_id=job_id,
                status="FAILED",
                end_timestamp=True
            )

        except Exception as update_error:

            print(
                f"Could not update failed job: "
                f"{str(update_error)}"
            )


def job_worker():

    print(
        "JOBSUMM worker started"
    )

    while True:

        try:

            job = get_pending_job()

            if job:

                print(
                    f"Pending JOB_ID found: "
                    f"{job['JOB_ID']}"
                )

                process_job(job)

            else:

                time.sleep(5)

        except Exception as e:

            print(
                f"Worker error: {str(e)}"
            )

            time.sleep(5)


# ============================================================
# Save Forecast Results into HANA
# ============================================================

def save_forecast_results(forecasts):

    connection = get_hana_connection()
    cursor = connection.cursor()

    run_id = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    run_date = date.today()

    sql = f"""
        INSERT INTO {FORECAST_RESULTS_TABLE}
        (
            "RUN_ID",
            "FORECAST_DATE",
            "COMMODITY",
            "MODEL",
            "PREDICTED_VALUE",
            "UNIT",
            "RUN_DATE"
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """

    saved = 0

    try:

        for forecast in forecasts:

            cursor.execute(
                sql,
                (
                    run_id,
                    forecast["date"],
                    "Gulf Gasoline",
                    "XGBoost",
                    float(
                        forecast["predicted_value"]
                    ),
                    "USD/gal",
                    run_date
                )
            )

            saved += 1

        connection.commit()

    except Exception:

        connection.rollback()
        raise

    finally:

        cursor.close()
        connection.close()

    return {
        "run_id": run_id,
        "records_saved": saved
    }


# ============================================================
# Save Job Forecast Results into FORCORR
# ============================================================

def save_job_forecast_results(
    job,
    forecasts,
    metadata_row
):

    connection = get_hana_connection()
    cursor = connection.cursor()

    sql = f"""
        INSERT INTO {FORCORR_TABLE}
        (
            "DCSID",
            "MIC",
            "PRICETYPE",
            "MKEYDT",
            "PRICEDATE",
            "JOB_ID",
            "FORCORR",
            "PRICE",
            "PER",
            "UOM",
            "CURRENCY",
            "CREATEDBY",
            "CREATEDAT"
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    completion_time = datetime.now()

    saved = 0

    try:

        for forecast in forecasts:

            cursor.execute(
                sql,
                (
                    job["TARDCSID"],
                    job["TARMIC"],
                    metadata_row["PRICETYPE"],
                    metadata_row["MKEYDT"],
                    forecast["date"],
                    job["JOB_ID"],
                    job["FORCORR"],
                    float(
                        forecast["predicted_value"]
                    ),
                    int(
                        metadata_row["PER"]
                    )
                    if pd.notna(
                        metadata_row["PER"]
                    )
                    else None,
                    metadata_row["UOM"],
                    metadata_row["CURRENCY"],
                    job["CREATEDBY"],
                    completion_time
                )
            )

            saved += 1

        connection.commit()

    except Exception:

        connection.rollback()
        raise

    finally:

        cursor.close()
        connection.close()

    return {
        "records_saved": saved
    }


# ============================================================
# Start JOBSUMM Worker
# ============================================================

worker_thread = threading.Thread(
    target=job_worker,
    daemon=True
)

worker_thread.start()


# ============================================================
# Get Destination Service credentials
# ============================================================

def get_destination_credentials():

    services = get_vcap_services()

    for service_type, service_list in services.items():

        for service in service_list:

            if (
                service.get("name")
                == "Forecast_model_cap-destination"
            ):

                credentials = service.get(
                    "credentials"
                )

                if credentials:
                    return credentials

    # Fallback
    for service_type, service_list in services.items():

        if service_type.lower() == "destination":

            for service in service_list:

                credentials = service.get(
                    "credentials"
                )

                if credentials:
                    return credentials

    raise Exception(
        "Destination Service credentials not found "
        "in VCAP_SERVICES"
    )


# ============================================================
# Get OAuth token from Destination Service
# ============================================================

def get_destination_token(credentials):

    client_id = credentials.get(
        "clientid"
    )

    client_secret = credentials.get(
        "clientsecret"
    )

    if not client_id:

        raise Exception(
            "Destination Service clientid not found"
        )

    if not client_secret:

        raise Exception(
            "Destination Service clientsecret not found"
        )

    token_url = credentials.get(
        "token_url"
    )

    if not token_url:

        base_url = credentials.get(
            "url"
        )

        if base_url:

            token_url = (
                base_url.rstrip("/")
                + "/oauth/token"
            )

    if not token_url:

        raise Exception(
            "Destination Service token URL not found"
        )

    response = requests.post(
        token_url,
        auth=(
            client_id,
            client_secret
        ),
        data={
            "grant_type": "client_credentials"
        },
        timeout=30
    )

    response.raise_for_status()

    return response.json()[
        "access_token"
    ]


# ============================================================
# Retrieve destination
# ============================================================

def get_destination():

    credentials = get_destination_credentials()

    token = get_destination_token(
        credentials
    )

    destination_url = credentials.get(
        "uri"
    )

    if not destination_url:

        raise Exception(
            "Destination Service URI not found "
            "in credentials"
        )

    url = (
        destination_url.rstrip("/")
        + "/destination-configuration/v1/destinations/"
        + DESTINATION_NAME
    )

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}"
        },
        timeout=30
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# Fetch WTI from EIA
# ============================================================

def fetch_wti():

    destination = get_destination()

    destination_properties = destination.get(
        "destinationConfiguration",
        {}
    )

    eia_url = destination_properties.get(
        "URL"
    )

    if not eia_url:

        raise Exception(
            "URL not found in EIA_WTI_API destination"
        )

    eia_key = destination_properties.get(
        "URL.queries.api_key"
    )

    if not eia_key:

        raise Exception(
            "URL.queries.api_key not found in "
            "EIA_WTI_API destination"
        )

    params = {
        "api_key": eia_key,
        "frequency": "daily",
        "data[0]": "value",
        "facets[series][]": "RWTC",
        "start": "2005-01-01",
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": 5000,
        "offset": 0
    }

    rows = []

    while True:

        response = requests.get(
            eia_url,
            params=params,
            timeout=60
        )

        response.raise_for_status()

        response_data = response.json()

        data = (
            response_data
            .get("response", {})
            .get("data", [])
        )

        if not data:
            break

        rows.extend(data)

        if len(data) < params["length"]:
            break

        params["offset"] += params["length"]

    return rows


# ============================================================
# Fetch FRED currency data
# ============================================================

def fetch_fred_series(series_id):

    credentials = get_destination_credentials()

    token = get_destination_token(
        credentials
    )

    destination_url = credentials.get(
        "uri"
    )

    if not destination_url:

        raise Exception(
            "Destination Service URI not found "
            "in credentials"
        )

    url = (
        destination_url.rstrip("/")
        + "/destination-configuration/v1/destinations/"
        + FRED_DESTINATION_NAME
    )

    response = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}"
        },
        timeout=30
    )

    response.raise_for_status()

    destination = response.json()

    destination_properties = destination.get(
        "destinationConfiguration",
        {}
    )

    fred_url = destination_properties.get(
        "URL"
    )

    if not fred_url:

        raise Exception(
            "URL not found in FRED_API destination"
        )

    fred_key = destination_properties.get(
        "URL.queries.api_key"
    )

    if not fred_key:

        raise Exception(
            "URL.queries.api_key not found in "
            "FRED_API destination"
        )

    params = {
        "series_id": series_id,
        "api_key": fred_key,
        "file_type": "json",
        "observation_start": "2005-01-01"
    }

    response = requests.get(
        fred_url.rstrip("/")
        + "/fred/series/observations",
        params=params,
        timeout=60
    )

    response.raise_for_status()

    return response.json().get(
        "observations",
        []
    )


# ============================================================
# Insert / UPSERT WTI data
# ============================================================

def insert_wti(rows):

    connection = get_hana_connection()
    cursor = connection.cursor()

    sql = f"""
        UPSERT {TABLE_NAME}
        ("DATE", "VALUE")
        VALUES (?, ?)
        WITH PRIMARY KEY
    """

    inserted = 0

    try:

        for row in rows:

            date_value = row.get(
                "period"
            )

            price_value = row.get(
                "value"
            )

            if (
                not date_value
                or price_value is None
            ):
                continue

            cursor.execute(
                sql,
                (
                    date_value,
                    float(price_value)
                )
            )

            inserted += 1

        connection.commit()

    except Exception:

        connection.rollback()
        raise

    finally:

        cursor.close()
        connection.close()

    return inserted


# ============================================================
# Insert / UPSERT FRED data
# ============================================================

def insert_fred_series(
    rows,
    series_id,
    variable_name,
    unit
):

    connection = get_hana_connection()
    cursor = connection.cursor()

    sql = f"""
        UPSERT {FRED_TABLE_NAME}
        (
            "DATE",
            "SERIES_ID",
            "VARIABLE_NAME",
            "VALUE",
            "UNIT",
            "SOURCE"
        )
        VALUES (?, ?, ?, ?, ?, ?)
        WITH PRIMARY KEY
    """

    inserted = 0

    try:

        for row in rows:

            date_value = row.get(
                "date"
            )

            value = row.get(
                "value"
            )

            if not date_value:
                continue

            if value in (
                None,
                "",
                "."
            ):
                continue

            cursor.execute(
                sql,
                (
                    date_value,
                    series_id,
                    variable_name,
                    float(value),
                    unit,
                    "FRED"
                )
            )

            inserted += 1

        connection.commit()

    except Exception:

        connection.rollback()
        raise

    finally:

        cursor.close()
        connection.close()

    return inserted


# ============================================================
# Test JOBSUMM
# ============================================================

@app.route("/test-jobsumm")
def test_jobsumm():

    try:

        job = get_pending_job()

        return jsonify({
            "status": "success",
            "job": job
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# Test JOBSUMM Update
# ============================================================

@app.route("/test-jobsumm-update")
def test_jobsumm_update():

    try:

        job = get_pending_job()

        if not job:

            return jsonify({
                "status": "error",
                "message": "No PENDING job found"
            }), 404

        job_id = job["JOB_ID"]

        update_job(
            job_id=job_id,
            status="RUNNING",
            completion=25,
            start_timestamp=True
        )

        return jsonify({
            "status": "success",
            "job_id": job_id,
            "new_status": "RUNNING",
            "completion": 25
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# Root endpoint
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "status": "success",
        "service": "EIA WTI Data Ingestion",
        "destination": DESTINATION_NAME
    })


# ============================================================
# Health endpoint
# ============================================================

@app.route("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# ============================================================
# Test HANA connection
# ============================================================

@app.route("/db-test", methods=["GET"])
def db_test():

    connection = None
    cursor = None

    try:

        connection = get_hana_connection()

        cursor = connection.cursor()

        cursor.execute(
            "SELECT CURRENT_USER FROM DUMMY"
        )

        result = cursor.fetchone()

        return jsonify({
            "status": "success",
            "database_user": result[0]
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ============================================================
# Test Flat File read
# ============================================================

@app.route("/test-flat-file")
def test_flat_file():

    try:

        df = read_flat_file_from_hana()

        return jsonify({
            "status": "success",
            "records": len(df),
            "columns": list(df.columns),
            "data": df.head(
                5
            ).to_dict(
                orient="records"
            )
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# Test Destination Service
# ============================================================

@app.route("/test-destination")
def test_destination():

    try:

        destination = get_destination()

        config = destination.get(
            "destinationConfiguration",
            {}
        )

        safe_response = {
            "status": "success",
            "destination": DESTINATION_NAME,
            "url": config.get("URL"),
            "type": config.get("Type"),
            "proxy_type": config.get("ProxyType"),
            "authentication": config.get(
                "Authentication"
            )
        }

        return jsonify(
            safe_response
        )

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# Fetch and insert WTI
# ============================================================

@app.route("/fetch-wti")
def fetch_wti_endpoint():

    try:

        rows = fetch_wti()

        inserted = insert_wti(
            rows
        )

        return jsonify({
            "status": "success",
            "source": "EIA",
            "series": "RWTC",
            "destination": DESTINATION_NAME,
            "records_fetched": len(rows),
            "records_inserted": inserted
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# Fetch and insert FRED currency data
# ============================================================

@app.route("/fetch-fred")
def fetch_fred_endpoint():

    try:

        series_config = [

            {
                "series_id": "DEXUSEU",
                "variable_name": "usd_per_eur",
                "unit": "USD/EUR"
            },

            {
                "series_id": "DEXCHUS",
                "variable_name": "cny_per_usd",
                "unit": "CNY/USD"
            },

            {
                "series_id": "DEXJPUS",
                "variable_name": "jpy_per_usd",
                "unit": "JPY/USD"
            }

        ]

        total_fetched = 0
        total_inserted = 0

        results = []

        for config in series_config:

            rows = fetch_fred_series(
                config["series_id"]
            )

            inserted = insert_fred_series(
                rows,
                config["series_id"],
                config["variable_name"],
                config["unit"]
            )

            total_fetched += len(rows)
            total_inserted += inserted

            results.append({
                "series_id":
                    config["series_id"],

                "variable_name":
                    config["variable_name"],

                "records_fetched":
                    len(rows),

                "records_inserted":
                    inserted
            })

        return jsonify({
            "status": "success",
            "source": "FRED",
            "destination": FRED_DESTINATION_NAME,
            "total_records_fetched":
                total_fetched,
            "total_records_inserted":
                total_inserted,
            "series": results
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# Forecast Gulf Gasoline
# ============================================================

@app.route("/forecast-gulf-gasoline")
def forecast_gulf_gasoline():

    try:

        forecasts = train_and_forecast(
            horizon=30
        )

        save_result = save_forecast_results(
            forecasts
        )

        return jsonify({
            "status": "success",
            "model": "XGBoost",
            "target":
                "gulf_gasoline_usd_gal",
            "run_id":
                save_result["run_id"],
            "records_saved":
                save_result["records_saved"],
            "forecast":
                forecasts
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# Forecast Flat File
# ============================================================

@app.route(
    "/forecast-flat-file",
    methods=["POST"]
)
def forecast_flat_file():

    try:

        if "file" not in request.files:

            return jsonify({
                "status": "error",
                "message":
                    "CSV file is required"
            }), 400

        file = request.files["file"]

        if not file.filename:

            return jsonify({
                "status": "error",
                "message":
                    "Filename is missing"
            }), 400

        dcsid = request.form.get(
            "dcsid"
        )

        mic = request.form.get(
            "mic"
        )

        pricetype = request.form.get(
            "pricetype"
        )

        if not dcsid:

            return jsonify({
                "status": "error",
                "message":
                    "DCSID is required"
            }), 400

        if not mic:

            return jsonify({
                "status": "error",
                "message":
                    "MIC is required"
            }), 400

        df = pd.read_csv(
            file
        )

        forecasts = (
            train_and_forecast_flat_file(
                df=df,
                dcsid=dcsid,
                mic=mic,
                pricetype=pricetype,
                horizon=30
            )
        )

        save_result = save_forecast_results(
            forecasts
        )

        return jsonify({
            "status": "success",
            "model": "XGBoost",
            "target": "PRICE",
            "date_column": "PRICEDATE",
            "filters": {
                "dcsid": dcsid,
                "mic": mic,
                "pricetype":
                    pricetype
            },
            "run_id":
                save_result["run_id"],
            "records_saved":
                save_result["records_saved"],
            "forecast":
                forecasts
        })

    except Exception as e:

        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


# ============================================================
# Local development
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000
    )