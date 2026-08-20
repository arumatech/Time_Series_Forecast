"""
Incremental Oil Market Data Updater for SAP BTP / CAP

Purpose
-------
This file is the DAILY/incremental companion to fetch_oil_data.py.

IMPORTANT:
- Do NOT use this file as the historical/base loader.
- fetch_oil_data.py remains untouched and is responsible for the initial
  historical load.
- This file is intended to run from SAP Job Scheduling Service through a
  Cloud Foundry Task.

The updater automatically:
1. Reads the latest PERIOD/YEAR already stored in CAP/HANA.
2. Creates a small overlap window to protect against source revisions.
3. Fetches only the required recent data from EIA/FRED/Yahoo/World Bank.
4. UPSERTS records into the CAP OData entities using OData PUT.
5. Retries transient network/server failures.
6. Continues with other independent datasets when one dataset fails.
7. Exits with a non-zero status if any dataset ultimately fails, so the
   scheduler/task can report the run as failed.

Datasets
--------
Daily:
    - EIA_PRICES_DAILY
    - FRED_CURRENCY_DAILY
    - YAHOO_FUTURES_DAILY

Weekly:
    - EIA_INVENTORY_WEEKLY
    - EIA_REFINERY_UTIL_WEEKLY
    - EIA_IMPORTS_EXPORTS_WEEKLY

Monthly:
    - FRED_INFLATION_MONTHLY

Annual:
    - WORLDBANK_MACRO_ANNUAL

Run locally with bindings:
    cds bind --exec -- python python/update_oil_data.py

Production:
    The Cloud Foundry Task should provide VCAP_SERVICES and
    CAP_SERVICE_URL through the same service bindings/environment used by
    the existing fetch_oil_data.py.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

import pandas as pd
import requests
import yfinance as yf


# =============================================================================
# 1. INCREMENTAL UPDATE CONFIGURATION
# =============================================================================

# Number of days to look backwards from the latest HANA date.
# This is intentionally small: it protects against revised source values
# without downloading the historical five-year baseline again.
OVERLAP_DAYS = 3

# When a table is unexpectedly empty, do NOT silently perform a five-year
# historical load. The historical loader is fetch_oil_data.py.
# Instead, the updater fetches only a small recent recovery window.
EMPTY_TABLE_RECOVERY_DAYS = 7

# Retries for transient HTTP/network errors.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0

# HTTP/network settings.
REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 0.15

# EIA pagination.
PAGE_SIZE = 5000

# Decimal precision matching the current CDS model.
DECIMAL_PLACES = 6


# =============================================================================
# 2. SAP BTP DESTINATIONS / CAP SERVICE
# =============================================================================

EIA_DESTINATION_NAME = "EIA_API"
FRED_DESTINATION_NAME = "FRED_API"
WORLD_BANK_DESTINATION_NAME = "WORLD_BANK_API"

CAP_SERVICE_URL = os.environ.get("CAP_SERVICE_URL")
if not CAP_SERVICE_URL:
    raise RuntimeError("CAP_SERVICE_URL environment variable is not configured.")

CAP_SERVICE_BASE_PATH = "/odata/v4/oil"


# =============================================================================
# 3. EIA CONFIGURATION
# =============================================================================

EIA_PRICES_ROUTE = "petroleum/pri/spt"
EIA_INVENTORY_ROUTE = "petroleum/stoc/wstk"
EIA_REFINERY_UTIL_ROUTE = "petroleum/pnp/wiup"
EIA_IMPORTS_EXPORTS_ROUTE = "petroleum/move/wkly"

EIA_PRICE_SERIES = {
    "RWTC": "wti_crude_usd_bbl",
    "RBRTE": "brent_crude_usd_bbl",
    "EER_EPMRR_PF4_Y05LA_DPG": "la_rbob_carbob_usd_gal",
    "EER_EPMRU_PF4_RGC_DPG": "gulf_gasoline_usd_gal",
    "EER_EPMRU_PF4_Y35NY_DPG": "nyh_gasoline_usd_gal",
    "EER_EPD2DXL0_PF4_RGC_DPG": "gulf_ulsd_diesel_usd_gal",
    "EER_EPJK_PF4_RGC_DPG": "gulf_jetfuel_usd_gal",
}

EIA_INVENTORY_SERIES = {
    "WCESTUS1": "crude_stocks_excl_spr_kbbl",
    "WCSSTUS1": "crude_stocks_spr_kbbl",
    "WGTSTUS1": "total_gasoline_stocks_kbbl",
    "WD0ST_NUS_1": "distillate_stocks_kbbl",
}

EIA_REFINERY_UTIL_SERIES = {
    "WPULEUS3": "refinery_utilization_pct",
}

EIA_IMPORTS_EXPORTS_SERIES = {
    "WCEIMUS2": "crude_imports_kbbl_d",
    "WCREXUS2": "crude_exports_kbbl_d",
}


# =============================================================================
# 4. FRED CONFIGURATION
# =============================================================================

FRED_OBSERVATIONS_PATH = "/fred/series/observations"

FRED_CURRENCY_SERIES = {
    "DTWEXBGS": "usd_broad_index",
    "DEXUSEU": "usd_per_eur",
    "DEXCHUS": "cny_per_usd",
    "DEXJPUS": "jpy_per_usd",
}

FRED_INFLATION_SERIES = {
    "CPIAUCSL": "cpi_all_urban",
    "CPIENGSL": "cpi_energy",
    "PPIACO": "ppi_all_commodities",
    "FEDFUNDS": "fed_funds_rate",
}


# =============================================================================
# 5. YAHOO FINANCE CONFIGURATION
# =============================================================================

YAHOO_FUTURES = {
    "CL=F": "wti_future",
    "BZ=F": "brent_future",
    "RB=F": "rbob_gasoline_future",
    "HO=F": "heating_oil_future",
    "NG=F": "natgas_future",
    "DX-Y.NYB": "dollar_index",
}


# =============================================================================
# 6. WORLD BANK CONFIGURATION
# =============================================================================

WORLD_BANK_INDICATORS = {
    "NY.GDP.MKTP.KD.ZG": "gdp_growth",
    "FP.CPI.TOTL.ZG": "inflation",
}

WORLD_BANK_COUNTRIES = {
    "USA": "usa",
    "WLD": "world",
}


# =============================================================================
# 7. DATASET METADATA
# =============================================================================

# Entity -> CAP key field -> update frequency.
DATASET_META: dict[str, dict[str, str]] = {
    "EIA_PRICES_DAILY": {"key": "PERIOD", "frequency": "daily"},
    "EIA_INVENTORY_WEEKLY": {"key": "PERIOD", "frequency": "weekly"},
    "EIA_REFINERY_UTIL_WEEKLY": {"key": "PERIOD", "frequency": "weekly"},
    "EIA_IMPORTS_EXPORTS_WEEKLY": {"key": "PERIOD", "frequency": "weekly"},
    "FRED_CURRENCY_DAILY": {"key": "PERIOD", "frequency": "daily"},
    "FRED_INFLATION_MONTHLY": {"key": "PERIOD", "frequency": "monthly"},
    "YAHOO_FUTURES_DAILY": {"key": "PERIOD", "frequency": "daily"},
    "WORLDBANK_MACRO_ANNUAL": {"key": "YEAR", "frequency": "annual"},
}


# =============================================================================
# 8. GENERIC HELPERS
# =============================================================================


def sleep_backoff(attempt: int) -> None:
    """Sleep using simple exponential backoff."""
    time.sleep(RETRY_BACKOFF_SECONDS * (2 ** (attempt - 1)))


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int = REQUEST_TIMEOUT,
    expected_statuses: set[int] | None = None,
    operation: str = "HTTP request",
) -> requests.Response:
    """Perform an HTTP request with bounded retries for transient failures."""

    expected_statuses = expected_statuses or {200}
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = session.request(
                method=method,
                url=url,
                params=params,
                headers=headers,
                json=json_body,
                timeout=timeout,
            )

            # Success / expected response.
            if response.status_code in expected_statuses:
                return response

            # Retry only transient conditions.
            if response.status_code == 429 or response.status_code >= 500:
                print(
                    f"[RETRY] {operation}: HTTP {response.status_code} "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
                if attempt < MAX_RETRIES:
                    retry_after = response.headers.get("Retry-After")
                    if retry_after:
                        try:
                            time.sleep(float(retry_after))
                        except ValueError:
                            sleep_backoff(attempt)
                    else:
                        sleep_backoff(attempt)
                    continue

            # Non-transient HTTP error.
            detail = response.text[:1000]
            raise RuntimeError(
                f"{operation} failed with HTTP {response.status_code}: {detail}"
            )

        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            print(
                f"[RETRY] {operation}: {type(exc).__name__} "
                f"(attempt {attempt}/{MAX_RETRIES})"
            )
            if attempt < MAX_RETRIES:
                sleep_backoff(attempt)
                continue

    raise RuntimeError(
        f"{operation} failed after {MAX_RETRIES} attempts: {last_error}"
    )


def round_numeric_columns(
    dataframe: pd.DataFrame,
    columns: list[str],
) -> pd.DataFrame:
    result = dataframe.copy()
    for column in columns:
        if column in result.columns:
            result[column] = pd.to_numeric(
                result[column], errors="coerce"
            ).round(DECIMAL_PLACES)
    return result


def dataframe_to_json_records(
    dataframe: pd.DataFrame,
    date_column: str = "period",
) -> list[dict[str, Any]]:
    result = dataframe.copy()

    if date_column in result.columns:
        result[date_column] = pd.to_datetime(
            result[date_column], errors="coerce"
        ).dt.strftime("%Y-%m-%d")

    result = result.astype(object).where(pd.notna(result), None)
    return result.to_dict(orient="records")


def parse_date(value: Any) -> date:
    parsed = pd.to_datetime(value, errors="raise")
    return parsed.date()


def format_date(value: date) -> str:
    return value.isoformat()


def frequency_overlap_start(latest_value: Any, frequency: str) -> str:
    """Return the start of a small overlap/recovery window."""
    if latest_value is None:
        # Baseline should already have been loaded by fetch_oil_data.py.
        # For safety, recover only a small recent window instead of silently
        # performing a five-year load. World Bank is annual, so use a bounded
        # five-year recovery window in that case.
        if frequency == "annual":
            return str(date.today().year - 5)
        return format_date(date.today() - timedelta(days=EMPTY_TABLE_RECOVERY_DAYS))

    latest_date = parse_date(latest_value)

    # A few calendar days is sufficient for daily/weekly/monthly data because
    # the external APIs return their actual available observations.
    # For annual data, the overlap is handled separately by year.
    if frequency == "annual":
        return str(latest_date.year - 1)

    return format_date(latest_date - timedelta(days=OVERLAP_DAYS))


# =============================================================================
# 9. SAP BTP DESTINATION SERVICE
# =============================================================================


def get_destination(
    destination_name: str,
    api_key_property: str | None = None,
) -> tuple[requests.Session, dict[str, str]]:
    """Load a subaccount destination through SAP BTP Destination Service."""

    print(f"[DESTINATION] Loading destination: {destination_name}")

    vcap_services = os.environ.get("VCAP_SERVICES")
    if not vcap_services:
        raise RuntimeError(
            "VCAP_SERVICES is not available. Run with cds bind or a bound CF task."
        )

    services = json.loads(vcap_services)
    destination_services = services.get("destination", [])
    if not destination_services:
        raise RuntimeError("No Destination Service binding was found.")

    credentials = destination_services[0].get("credentials", {})
    required = ["clientid", "clientsecret", "uri", "url"]
    missing = [key for key in required if not credentials.get(key)]
    if missing:
        raise RuntimeError(
            f"Destination Service credentials are missing: {missing}"
        )

    destination_service_url = credentials["uri"]
    xsuaa_url = credentials["url"]

    # The token endpoint requires HTTP Basic auth, so this request is kept
    # explicit and bounded rather than using request_with_retry().
    access_token = None
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            token_response = requests.post(
                f"{xsuaa_url}/oauth/token",
                data={"grant_type": "client_credentials"},
                auth=(credentials["clientid"], credentials["clientsecret"]),
                timeout=30,
            )
            if token_response.status_code == 200:
                access_token = token_response.json().get("access_token")
                break
            if token_response.status_code >= 500 or token_response.status_code == 429:
                print(
                    f"[RETRY] Destination Service token request: "
                    f"HTTP {token_response.status_code} "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
                if attempt < MAX_RETRIES:
                    sleep_backoff(attempt)
                    continue
            raise RuntimeError(
                "Destination Service token request failed: "
                f"HTTP {token_response.status_code}: {token_response.text[:1000]}"
            )
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                sleep_backoff(attempt)
                continue
            raise RuntimeError(
                f"Destination Service token request failed: {exc}"
            ) from exc

    if not access_token:
        raise RuntimeError(
            f"Destination Service token request failed after retries: {last_error}"
        )

    destination_url = (
        f"{destination_service_url}"
        f"/destination-configuration/v2/destinations/"
        f"{destination_name}@subaccount"
    )

    response = request_with_retry(
        requests.Session(),
        "GET",
        destination_url,
        headers={"Authorization": f"Bearer {access_token}"},
        expected_statuses={200},
        operation=f"Load destination {destination_name}",
    )

    destination = response.json().get("destinationConfiguration")
    if not destination:
        raise RuntimeError(
            f"Destination '{destination_name}' was not returned."
        )

    base_url = destination.get("URL")
    if not base_url:
        raise RuntimeError(
            f"Destination '{destination_name}' does not contain a URL."
        )

    result = {"base_url": base_url}

    if api_key_property:
        api_key = destination.get(api_key_property)
        if not api_key:
            raise RuntimeError(
                f"API key was not found in destination property '{api_key_property}'."
            )
        result["api_key"] = api_key

    return requests.Session(), result


def get_eia_http_client():
    return get_destination(EIA_DESTINATION_NAME, "URL.queries.api_key")


def get_fred_http_client():
    return get_destination(FRED_DESTINATION_NAME, "URL.queries.api_key")


def get_world_bank_http_client():
    return get_destination(WORLD_BANK_DESTINATION_NAME)


# =============================================================================
# 10. XSUAA / CAP
# =============================================================================


def get_xsuaa_access_token() -> str:
    """Get a fresh client-credentials OAuth token for the CAP service."""

    vcap_services = os.environ.get("VCAP_SERVICES")
    if not vcap_services:
        raise RuntimeError("VCAP_SERVICES is not available.")

    services = json.loads(vcap_services)
    xsuaa_services = services.get("xsuaa", [])
    if not xsuaa_services:
        raise RuntimeError("No XSUAA service binding was found.")

    credentials = xsuaa_services[0].get("credentials", {})
    required = ["url", "clientid", "clientsecret"]
    missing = [key for key in required if not credentials.get(key)]
    if missing:
        raise RuntimeError(f"XSUAA credentials are missing: {missing}")

    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                f"{credentials['url']}/oauth/token",
                data={"grant_type": "client_credentials"},
                auth=(credentials["clientid"], credentials["clientsecret"]),
                timeout=30,
            )

            if response.status_code == 200:
                token = response.json().get("access_token")
                if not token:
                    raise RuntimeError("XSUAA did not return an access token.")
                return token

            if response.status_code >= 500 or response.status_code == 429:
                print(
                    f"[RETRY] XSUAA token request: HTTP {response.status_code} "
                    f"(attempt {attempt}/{MAX_RETRIES})"
                )
                if attempt < MAX_RETRIES:
                    sleep_backoff(attempt)
                    continue

            raise RuntimeError(
                f"XSUAA token request failed: HTTP {response.status_code}: "
                f"{response.text[:1000]}"
            )

        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                sleep_backoff(attempt)
                continue

    raise RuntimeError(
        f"XSUAA token request failed after retries: {last_error}"
    )


def get_cap_headers() -> dict[str, str]:
    token = get_xsuaa_access_token()
    return {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }


def cap_entity_url(entity_name: str) -> str:
    return f"{CAP_SERVICE_URL}{CAP_SERVICE_BASE_PATH}/{entity_name}"


# =============================================================================
# 11. READ LATEST KEY FROM CAP/HANA
# =============================================================================


def get_latest_key_from_cap(
    entity_name: str,
    key_field: str,
) -> str | None:
    """
    Read the latest stored key from CAP/HANA.

    We intentionally ask CAP rather than connecting Python directly to HANA.
    This keeps the existing architecture:

        Python -> CAP OData -> HANA
    """

    headers = get_cap_headers()
    url = cap_entity_url(entity_name)

    # IMPORTANT:
    # requests encodes spaces in query parameters as "+" (for example,
    # "$orderby=PERIOD+desc"). CAP's OData parser in this project does not
    # accept "+" for the OData $orderby expression, so build $orderby
    # explicitly with "%20" instead of passing it through requests' params.
    encoded_orderby = f"{key_field}%20desc"

    query_url = (
        f"{url}?$select={key_field}"
        f"&$orderby={encoded_orderby}"
        f"&$top=1"
    )

    response = request_with_retry(
        requests.Session(),
        "GET",
        query_url,
        headers=headers,
        expected_statuses={200},
        operation=f"Read latest {key_field} from {entity_name}",
    )

    payload = response.json()
    values = payload.get("value", [])

    if not values:
        return None

    latest = values[0].get(key_field)
    if latest is None:
        return None

    return str(latest)


# =============================================================================
# 12. UPSERT TO CAP USING ODATA PUT
# =============================================================================


def odata_key_literal(key_field: str, key_value: str) -> str:
    """
    Build an OData V4 key predicate for the current single-key CDS entities.

    PERIOD is a Date and YEAR is an Integer in the CDS model.
    """

    if key_field == "PERIOD":
        return f"{key_field}={key_value}"

    if key_field == "YEAR":
        return f"{key_field}={int(key_value)}"

    raise ValueError(f"Unsupported key field: {key_field}")


def upsert_record_to_cap(
    session: requests.Session,
    headers: dict[str, str],
    entity_name: str,
    key_field: str,
    record: dict[str, Any],
) -> str:
    """
    UPSERT one record using OData PUT to the entity's key URL.

    Result values:
        "upserted" -> PUT accepted
    """

    if key_field not in record or record[key_field] is None:
        raise ValueError(
            f"{entity_name}: record does not contain key field {key_field}."
        )

    key_value = str(record[key_field])
    key_predicate = odata_key_literal(key_field, key_value)
    url = f"{cap_entity_url(entity_name)}({key_predicate})"

    response = request_with_retry(
        session,
        "PUT",
        url,
        headers=headers,
        json_body=record,
        expected_statuses={200, 201, 204},
        operation=f"UPSERT {entity_name} {key_field}={key_value}",
    )

    # CAP/OData may return 200/201/204 for successful update/create.
    return "upserted"


def upsert_records_to_cap(
    records: list[dict[str, Any]],
    entity_name: str,
    key_field: str,
) -> tuple[int, int]:
    """UPSERT all records and return (processed, failed)."""

    if not records:
        print(f"[CAP] {entity_name}: no records to UPSERT.")
        return 0, 0

    print(
        f"[CAP] UPSERT {entity_name}: {len(records)} record(s)"
    )

    headers = get_cap_headers()
    session = requests.Session()

    processed = 0
    failed = 0

    for record in records:
        cap_record = {
            key.upper(): value
            for key, value in record.items()
            if value is not None
        }

        # Record keys are already uppercase after conversion, so normalize the
        # expected key field for lookup.
        cap_key_field = key_field.upper()

        try:
            if cap_key_field not in cap_record:
                raise ValueError(
                    f"Missing key field {cap_key_field} in record: {cap_record}"
                )

            upsert_record_to_cap(
                session=session,
                headers=headers,
                entity_name=entity_name,
                key_field=cap_key_field,
                record=cap_record,
            )
            processed += 1

        except Exception as exc:
            failed += 1
            print(
                f"[CAP] UPSERT FAILED - {entity_name} "
                f"{cap_key_field}={cap_record.get(cap_key_field)}: {exc}"
            )

    print(
        f"[CAP] {entity_name}: processed={processed}, failed={failed}"
    )

    return processed, failed


# =============================================================================
# 13. EIA FETCH
# =============================================================================


def fetch_eia_series(
    http_client: requests.Session,
    api_key: str,
    base_url: str,
    route: str,
    series_id: str,
    column_name: str,
    frequency: str,
    start_date: str,
) -> pd.Series | None:
    params: dict[str, Any] = {
        "api_key": api_key,
        "frequency": frequency,
        "data[0]": "value",
        "facets[series][]": series_id,
        "start": start_date,
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": PAGE_SIZE,
        "offset": 0,
    }

    rows: list[dict[str, Any]] = []
    url = f"{base_url}/{route}/data/"

    print(
        f"[EIA] {series_id} -> {column_name} "
        f"from {start_date}"
    )

    while True:
        response = request_with_retry(
            http_client,
            "GET",
            url,
            params=params,
            expected_statuses={200},
            operation=f"EIA {series_id}",
        )

        data = response.json().get("response", {}).get("data", [])
        if not data:
            break

        rows.extend(data)

        if len(data) < params["length"]:
            break

        params["offset"] += params["length"]
        time.sleep(REQUEST_DELAY_SECONDS)

    if not rows:
        return None

    frame = pd.DataFrame(rows)
    frame["period"] = pd.to_datetime(frame["period"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["period", "value"])

    return (
        frame.drop_duplicates(subset=["period"])
        .sort_values("period")
        .set_index("period")["value"]
        .rename(column_name)
    )


def combine_eia_series(
    series_config: dict[str, str],
    route: str,
    frequency: str,
    dataset_name: str,
    start_date: str,
) -> pd.DataFrame:
    http_client, credentials = get_eia_http_client()
    series_frames: list[pd.Series] = []

    for series_id, column_name in series_config.items():
        series = fetch_eia_series(
            http_client=http_client,
            api_key=credentials["api_key"],
            base_url=credentials["base_url"],
            route=route,
            series_id=series_id,
            column_name=column_name,
            frequency=frequency,
            start_date=start_date,
        )
        if series is not None:
            series_frames.append(series)
        time.sleep(REQUEST_DELAY_SECONDS)

    if not series_frames:
        return pd.DataFrame()

    result = (
        pd.concat(series_frames, axis=1, sort=True)
        .sort_index()
        .reset_index()
    )

    return round_numeric_columns(
        result,
        list(series_config.values()),
    )


def fetch_eia_prices_daily(start_date: str) -> pd.DataFrame:
    return combine_eia_series(
        EIA_PRICE_SERIES,
        EIA_PRICES_ROUTE,
        "daily",
        "daily prices",
        start_date,
    )


def fetch_eia_inventory_weekly(start_date: str) -> pd.DataFrame:
    return combine_eia_series(
        EIA_INVENTORY_SERIES,
        EIA_INVENTORY_ROUTE,
        "weekly",
        "weekly inventory",
        start_date,
    )


def fetch_eia_refinery_util_weekly(start_date: str) -> pd.DataFrame:
    return combine_eia_series(
        EIA_REFINERY_UTIL_SERIES,
        EIA_REFINERY_UTIL_ROUTE,
        "weekly",
        "refinery utilization",
        start_date,
    )


def fetch_eia_imports_exports_weekly(start_date: str) -> pd.DataFrame:
    return combine_eia_series(
        EIA_IMPORTS_EXPORTS_SERIES,
        EIA_IMPORTS_EXPORTS_ROUTE,
        "weekly",
        "imports/exports",
        start_date,
    )


# =============================================================================
# 14. FRED FETCH
# =============================================================================


def fetch_fred_series(
    http_client: requests.Session,
    api_key: str,
    base_url: str,
    series_id: str,
    column_name: str,
    start_date: str,
) -> pd.Series | None:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start_date,
    }

    print(
        f"[FRED] {series_id} -> {column_name} "
        f"from {start_date}"
    )

    response = request_with_retry(
        http_client,
        "GET",
        f"{base_url}{FRED_OBSERVATIONS_PATH}",
        params=params,
        expected_statuses={200},
        operation=f"FRED {series_id}",
    )

    observations = response.json().get("observations", [])
    if not observations:
        return None

    frame = pd.DataFrame(observations)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["value"] = pd.to_numeric(frame["value"], errors="coerce")
    frame = frame.dropna(subset=["date", "value"])

    return (
        frame.drop_duplicates(subset=["date"])
        .sort_values("date")
        .set_index("date")["value"]
        .rename(column_name)
    )


def combine_fred_series(
    series_config: dict[str, str],
    dataset_name: str,
    start_date: str,
) -> pd.DataFrame:
    http_client, credentials = get_fred_http_client()
    frames: list[pd.Series] = []

    for series_id, column_name in series_config.items():
        series = fetch_fred_series(
            http_client=http_client,
            api_key=credentials["api_key"],
            base_url=credentials["base_url"],
            series_id=series_id,
            column_name=column_name,
            start_date=start_date,
        )
        if series is not None:
            frames.append(series)
        time.sleep(REQUEST_DELAY_SECONDS)

    if not frames:
        return pd.DataFrame()

    result = (
        pd.concat(frames, axis=1, sort=True)
        .sort_index()
        .reset_index()
        .rename(columns={"date": "period"})
    )

    return round_numeric_columns(
        result,
        list(series_config.values()),
    )


def fetch_fred_currency_daily(start_date: str) -> pd.DataFrame:
    return combine_fred_series(
        FRED_CURRENCY_SERIES,
        "daily currency",
        start_date,
    )


def fetch_fred_inflation_monthly(start_date: str) -> pd.DataFrame:
    return combine_fred_series(
        FRED_INFLATION_SERIES,
        "monthly inflation/macro",
        start_date,
    )


# =============================================================================
# 15. YAHOO FINANCE FETCH
# =============================================================================


def fetch_yahoo_futures_daily(start_date: str) -> pd.DataFrame:
    print(f"[YAHOO] Fetching daily futures from {start_date}...")

    # yfinance's end parameter is exclusive, so use tomorrow to include the
    # latest available observation when the run happens during the day.
    end_date = date.today() + timedelta(days=1)

    data = yf.download(
        list(YAHOO_FUTURES.keys()),
        start=start_date,
        end=end_date.isoformat(),
        auto_adjust=False,
        progress=False,
        group_by="ticker",
    )

    if data.empty:
        return pd.DataFrame()

    series_frames: list[pd.Series] = []

    for ticker, column_name in YAHOO_FUTURES.items():
        try:
            close = data[ticker]["Close"].copy()
        except (KeyError, TypeError):
            print(f"[YAHOO] WARNING: No Close data for {ticker}")
            continue

        close = pd.to_numeric(close, errors="coerce")
        close.index = pd.to_datetime(close.index, errors="coerce")
        close = close.dropna()
        close.name = column_name
        series_frames.append(close)

    if not series_frames:
        return pd.DataFrame()

    result = (
        pd.concat(series_frames, axis=1, sort=True)
        .sort_index()
        .reset_index()
    )

    # yfinance can return either Date or a differently named index column.
    if "Date" in result.columns:
        result = result.rename(columns={"Date": "period"})
    elif "index" in result.columns:
        result = result.rename(columns={"index": "period"})

    return round_numeric_columns(
        result,
        list(YAHOO_FUTURES.values()),
    )


# =============================================================================
# 16. WORLD BANK FETCH
# =============================================================================


def fetch_world_bank_indicator(
    http_client: requests.Session,
    base_url: str,
    country: str,
    indicator: str,
) -> pd.Series | None:
    url = (
        f"{base_url}/v2/country/"
        f"{country}/indicator/{indicator}"
    )

    params = {
        "format": "json",
        "per_page": 100,
    }

    print(f"[WORLD BANK] Fetching {country} / {indicator}")

    response = request_with_retry(
        http_client,
        "GET",
        url,
        params=params,
        expected_statuses={200},
        operation=f"World Bank {country}/{indicator}",
    )

    payload = response.json()
    if len(payload) < 2:
        return None

    rows = []
    for observation in payload[1]:
        year = observation.get("date")
        value = observation.get("value")
        if year is None or value is None:
            continue

        try:
            rows.append({"year": int(year), "value": float(value)})
        except (TypeError, ValueError):
            continue

    if not rows:
        return None

    frame = pd.DataFrame(rows)
    return (
        frame.drop_duplicates(subset=["year"])
        .sort_values("year")
        .set_index("year")["value"]
    )


def fetch_worldbank_macro_annual(start_year: int) -> pd.DataFrame:
    http_client, credentials = get_world_bank_http_client()
    datasets: dict[str, pd.Series] = {}

    for country, country_name in WORLD_BANK_COUNTRIES.items():
        for indicator, indicator_name in WORLD_BANK_INDICATORS.items():
            series = fetch_world_bank_indicator(
                http_client=http_client,
                base_url=credentials["base_url"],
                country=country,
                indicator=indicator,
            )

            if series is None:
                continue

            # Include a small one-year overlap and anything newer.
            series = series[series.index >= start_year]
            column_name = f"{country_name}_{indicator_name}"
            datasets[column_name] = series.rename(column_name)
            time.sleep(REQUEST_DELAY_SECONDS)

    if not datasets:
        return pd.DataFrame()

    result = (
        pd.concat(datasets.values(), axis=1, sort=True)
        .sort_index()
        .reset_index()
    )

    result = result.rename(
        columns={
            "year": "YEAR",
            "usa_gdp_growth": "USA_GDP_GROWTH_PCT",
            "usa_inflation": "USA_INFLATION_CPI_PCT",
            "world_gdp_growth": "WORLD_GDP_GROWTH_PCT",
            "world_inflation": "WORLD_INFLATION_CPI_PCT",
        }
    )

    return round_numeric_columns(
        result,
        [
            "USA_GDP_GROWTH_PCT",
            "USA_INFLATION_CPI_PCT",
            "WORLD_GDP_GROWTH_PCT",
            "WORLD_INFLATION_CPI_PCT",
        ],
    )


# =============================================================================
# 17. DATASET PROCESSING
# =============================================================================


def run_dataset(
    entity_name: str,
    fetch_function: Callable[[str], pd.DataFrame],
    *,
    key_field: str,
    frequency: str,
) -> dict[str, Any]:
    """Run one dataset end-to-end and return a status summary."""

    started = time.time()
    result: dict[str, Any] = {
        "entity": entity_name,
        "frequency": frequency,
        "status": "FAILED",
        "hana_latest": None,
        "fetch_start": None,
        "source_records": 0,
        "upserted": 0,
        "failed": 0,
        "error": None,
    }

    print("\n" + "=" * 72)
    print(f"DATASET: {entity_name} ({frequency})")
    print("=" * 72)

    try:
        latest = get_latest_key_from_cap(entity_name, key_field)
        result["hana_latest"] = latest or "EMPTY"

        start_value = frequency_overlap_start(latest, frequency)
        result["fetch_start"] = start_value

        print(f"[STATE] HANA latest {key_field}: {latest or 'NONE'}")
        print(f"[STATE] Fetch start with overlap: {start_value}")

        if frequency == "annual":
            dataframe = fetch_function(start_value)
        else:
            dataframe = fetch_function(start_value)

        if dataframe.empty:
            # No new observations is normal for weekly/monthly/annual sources.
            # For daily sources it can also happen on weekends/holidays.
            result["status"] = "UP_TO_DATE"
            print(f"[DATA] {entity_name}: no source records in update window.")
            return result

        result["source_records"] = len(dataframe)

        # Validate and normalize the key before sending anything to CAP.
        if key_field == "PERIOD":
            # All daily/weekly/monthly fetchers expose the source date as
            # lowercase `period`. Normalize it to the CDS key `PERIOD`.
            if "period" not in dataframe.columns and key_field not in dataframe.columns:
                raise RuntimeError(
                    f"{entity_name}: expected period column was not found."
                )

            source_key = "period" if "period" in dataframe.columns else key_field
            dataframe[key_field] = pd.to_datetime(
                dataframe[source_key], errors="coerce"
            )
            dataframe = dataframe.dropna(subset=[key_field])
            dataframe[key_field] = dataframe[key_field].dt.strftime("%Y-%m-%d")
        else:
            if key_field not in dataframe.columns:
                raise RuntimeError(
                    f"{entity_name}: expected key column {key_field} was not found."
                )

            dataframe[key_field] = pd.to_numeric(
                dataframe[key_field], errors="coerce"
            )
            dataframe = dataframe.dropna(subset=[key_field])
            dataframe[key_field] = dataframe[key_field].astype(int)

        # Keep only valid rows. Duplicate keys in the source are collapsed.
        dataframe = dataframe.drop_duplicates(subset=[key_field]).sort_values(key_field)

        if dataframe.empty:
            result["status"] = "UP_TO_DATE"
            print(f"[DATA] {entity_name}: no valid records after validation.")
            return result

        records = dataframe_to_json_records(
            dataframe,
            date_column="period" if key_field == "PERIOD" else "__no_date__",
        )

        # For World Bank, dataframe_to_json_records does not touch YEAR because
        # the date_column is intentionally absent.
        if key_field == "YEAR":
            records = [
                {
                    key: value
                    for key, value in record.items()
                    if value is not None
                }
                for record in records
            ]

        upserted, failed = upsert_records_to_cap(
            records,
            entity_name,
            key_field,
        )

        result["upserted"] = upserted
        result["failed"] = failed

        if failed:
            raise RuntimeError(
                f"{failed} record(s) failed to UPSERT."
            )

        result["status"] = "SUCCESS"
        return result

    except Exception as exc:
        result["status"] = "FAILED"
        result["error"] = str(exc)
        print(f"[DATASET FAILED] {entity_name}: {exc}")
        return result

    finally:
        result["duration_seconds"] = round(time.time() - started, 2)


# =============================================================================
# 18. MAIN
# =============================================================================


def main() -> None:
    print("=" * 72)
    print("OIL MARKET DATA - DAILY INCREMENTAL UPDATE")
    print("=" * 72)
    print(f"CAP_SERVICE_URL : {CAP_SERVICE_URL}")
    print(f"OVERLAP_DAYS    : {OVERLAP_DAYS}")
    print(f"MAX_RETRIES     : {MAX_RETRIES}")
    print(f"RUN DATE        : {datetime.now().isoformat(timespec='seconds')}")

    datasets: list[tuple[str, Callable[[str], pd.DataFrame], str, str]] = [
        (
            "EIA_PRICES_DAILY",
            fetch_eia_prices_daily,
            "PERIOD",
            "daily",
        ),
        (
            "EIA_INVENTORY_WEEKLY",
            fetch_eia_inventory_weekly,
            "PERIOD",
            "weekly",
        ),
        (
            "EIA_REFINERY_UTIL_WEEKLY",
            fetch_eia_refinery_util_weekly,
            "PERIOD",
            "weekly",
        ),
        (
            "EIA_IMPORTS_EXPORTS_WEEKLY",
            fetch_eia_imports_exports_weekly,
            "PERIOD",
            "weekly",
        ),
        (
            "FRED_CURRENCY_DAILY",
            fetch_fred_currency_daily,
            "PERIOD",
            "daily",
        ),
        (
            "FRED_INFLATION_MONTHLY",
            fetch_fred_inflation_monthly,
            "PERIOD",
            "monthly",
        ),
        (
            "YAHOO_FUTURES_DAILY",
            fetch_yahoo_futures_daily,
            "PERIOD",
            "daily",
        ),
    ]

    summaries: list[dict[str, Any]] = []

    # Process independent datasets even if another source fails.
    for entity_name, fetch_function, key_field, frequency in datasets:
        summaries.append(
            run_dataset(
                entity_name,
                fetch_function,
                key_field=key_field,
                frequency=frequency,
            )
        )

    # World Bank is annual and uses YEAR instead of PERIOD.
    # run_dataset() determines the latest YEAR and passes a one-year overlap
    # (or a bounded recovery year range if the table is empty) into this
    # function.
    def fetch_worldbank_for_update(start_year: str) -> pd.DataFrame:
        return fetch_worldbank_macro_annual(int(start_year))

    summaries.append(
        run_dataset(
            "WORLDBANK_MACRO_ANNUAL",
            fetch_worldbank_for_update,
            key_field="YEAR",
            frequency="annual",
        )
    )

    # -------------------------------------------------------------------------
    # Final summary
    # -------------------------------------------------------------------------
    print("\n" + "=" * 72)
    print("RUN SUMMARY")
    print("=" * 72)

    successful = 0
    failed = 0
    up_to_date = 0
    total_upserted = 0
    total_record_failures = 0

    for item in summaries:
        status = item["status"]
        if status == "SUCCESS":
            successful += 1
        elif status == "UP_TO_DATE":
            up_to_date += 1
        else:
            failed += 1

        total_upserted += int(item.get("upserted", 0))
        total_record_failures += int(item.get("failed", 0))

        print(
            f"{item['entity']:<32} "
            f"{status:<12} "
            f"HANA latest={item.get('hana_latest')} "
            f"upserted={item.get('upserted', 0)}"
        )
        if item.get("error"):
            print(f"    ERROR: {item['error']}")

    print("-" * 72)
    print(f"Successful datasets : {successful}")
    print(f"Up-to-date datasets : {up_to_date}")
    print(f"Failed datasets     : {failed}")
    print(f"UPSERT operations   : {total_upserted}")
    print(f"Record failures     : {total_record_failures}")

    if failed:
        print("OVERALL STATUS      : FAILED")
        # Non-zero exit status is important so Cloud Foundry Task / Job
        # Scheduling Service can identify the run as unsuccessful.
        raise RuntimeError(
            f"Daily incremental update failed for {failed} dataset(s)."
        )

    print("OVERALL STATUS      : SUCCESS")


if __name__ == "__main__":
    main()
