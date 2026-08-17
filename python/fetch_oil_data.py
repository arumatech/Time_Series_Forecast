"""
Oil Market Data Fetcher for SAP BTP / CAP

Sources
-------
1. EIA        -> daily prices + weekly inventory + refinery utilization + imports/exports
2. FRED       -> daily currency + monthly inflation/macro
3. Yahoo      -> daily futures
4. World Bank -> annual USA/world GDP growth + inflation

Architecture
------------
    Python
       |
       +--> SAP BTP Destination Service
       |       |
       |       +--> EIA
       |       +--> FRED
       |       +--> World Bank
       |
       +--> Yahoo Finance (direct)
       |
       +--> pandas
       |
       +--> JSON records
       |
       +--> XSUAA
       |
       +--> CAP OData service
       |
       +--> HANA

Run with:
    cds bind --exec -- python python/fetch_oil_data.py

IMPORTANT CONFIGURATION
-----------------------
The DATA FETCH CONTROL section below is the main place to change how much
data you want.

For daily/weekly EIA, FRED and Yahoo:
    START_DATE = "2024-01-01"
    END_DATE   = None          # None = up to latest available date
    MAX_ROWS   = 50            # 50 rows per final dataset; None = all

For World Bank:
    WORLD_BANK_START_YEAR = 1960
    WORLD_BANK_END_YEAR   = None
    WORLD_BANK_MAX_ROWS   = 50

Rounding:
    DECIMAL_PLACES = 6

Change DECIMAL_PLACES if the CDS Decimal scale changes in the future.

For production/all-data run:
    Set MAX_ROWS = None
    Set WORLD_BANK_MAX_ROWS = None

For testing:
    Keep MAX_ROWS = 50 (or any small number).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

import pandas as pd
import requests
import yfinance as yf


# =============================================================================
# 1. DATA FETCH CONTROL - CHANGE THESE VALUES
# =============================================================================

# ---------------------------------------------------------------------------
# Daily / weekly sources: EIA, FRED, Yahoo
# ---------------------------------------------------------------------------

START_DATE = "2024-01-01"

# None = fetch through the latest available date.
# Example: "2024-12-31" = stop at that date.
END_DATE: str | None = None

# Maximum final rows per dataset.
# 50 = useful for testing.
# None = keep all rows in the selected date range.
MAX_ROWS: int | None = 50


# ---------------------------------------------------------------------------
# World Bank - annual data
# ---------------------------------------------------------------------------

WORLD_BANK_START_YEAR = 1960

# None = fetch through the latest available year.
WORLD_BANK_END_YEAR: int | None = None

# Maximum final World Bank rows.
# 50 = testing.
# None = keep all years in the selected range.
WORLD_BANK_MAX_ROWS: int | None = 50


# ---------------------------------------------------------------------------
# Numeric precision
# ---------------------------------------------------------------------------

# CDS currently uses Decimal(15,6) for these datasets.
# Change this only if the CDS model changes.
DECIMAL_PLACES = 6


# ---------------------------------------------------------------------------
# General HTTP / pagination settings
# ---------------------------------------------------------------------------

PAGE_SIZE = 5000
REQUEST_TIMEOUT = 60
REQUEST_DELAY_SECONDS = 0.15


# =============================================================================
# 2. SAP BTP DESTINATIONS
# =============================================================================

EIA_DESTINATION_NAME = "EIA_API"
FRED_DESTINATION_NAME = "FRED_API"
WORLD_BANK_DESTINATION_NAME = "WORLD_BANK_API"


# =============================================================================
# 3. CAP SERVICE
# =============================================================================

# CAP_SERVICE_URL = (
#     "https://df9c95d5trial-dev-time-series-forecast-srv."
#     "cfapps.ap21.hana.ondemand.com"
# )

CAP_SERVICE_URL = os.environ.get("CAP_SERVICE_URL")

if not CAP_SERVICE_URL:
    raise RuntimeError(
        "CAP_SERVICE_URL environment variable is not configured."
    )

CAP_SERVICE_BASE_PATH = "/odata/v4/oil"


# =============================================================================
# 4. EIA CONFIGURATION
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
# 5. FRED CONFIGURATION
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
# 6. YAHOO FINANCE CONFIGURATION
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
# 7. WORLD BANK CONFIGURATION
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
# 8. COMMON HELPERS
# =============================================================================

def validate_date_range() -> None:
    """Validate the user-controlled date range."""

    if END_DATE is not None and END_DATE < START_DATE:
        raise ValueError(
            f"END_DATE ({END_DATE}) cannot be earlier than "
            f"START_DATE ({START_DATE})."
        )


def limit_dataframe(
    dataframe: pd.DataFrame,
    max_rows: int | None,
) -> pd.DataFrame:
    """
    Limit rows after sorting chronologically.

    This gives predictable testing behaviour:
        MAX_ROWS = 50 -> first 50 chronological records.

    None -> no row limit.
    """

    if max_rows is None:
        return dataframe

    if max_rows <= 0:
        raise ValueError("max_rows must be greater than 0 or None.")

    return dataframe.head(max_rows).copy()


def round_numeric_columns(
    dataframe: pd.DataFrame,
    columns: list[str],
    decimal_places: int = DECIMAL_PLACES,
) -> pd.DataFrame:
    """
    Convert selected columns to numeric and round them.

    Example:
        2.161381956 -> 2.161382

    This is kept in one place so precision can be changed easily.
    """

    result = dataframe.copy()

    for column in columns:
        if column in result.columns:
            result[column] = pd.to_numeric(
                result[column],
                errors="coerce",
            ).round(decimal_places)

    return result


def dataframe_to_json_records(
    dataframe: pd.DataFrame,
    date_column: str = "period",
) -> list[dict[str, Any]]:
    """Convert a DataFrame into JSON-compatible in-memory records."""

    result = dataframe.copy()

    if date_column in result.columns:
        result[date_column] = pd.to_datetime(
            result[date_column],
            errors="coerce",
        ).dt.strftime("%Y-%m-%d")

    # JSON cannot represent NaN correctly.
    result = result.astype(object).where(
        pd.notna(result),
        None,
    )

    return result.to_dict(orient="records")


# =============================================================================
# 9. SAP BTP DESTINATION SERVICE
# =============================================================================

def get_destination(
    destination_name: str,
    api_key_property: str | None = None,
) -> tuple[requests.Session, dict[str, str]]:
    """
    Load a subaccount destination through SAP BTP Destination Service.

    Authentication flow:
        VCAP_SERVICES
             ↓
        Destination Service credentials
             ↓
        XSUAA client-credentials token
             ↓
        Destination Service REST API
             ↓
        Requested subaccount destination
    """

    print(f"[DESTINATION] Loading destination: {destination_name}")

    vcap_services = os.environ.get("VCAP_SERVICES")

    if not vcap_services:
        raise RuntimeError(
            "VCAP_SERVICES is not available. Run with:\n"
            "cds bind --exec -- python python/fetch_oil_data.py"
        )

    services = json.loads(vcap_services)

    destination_services = services.get("destination", [])

    if not destination_services:
        raise RuntimeError(
            "No Destination Service binding was found."
        )

    credentials = destination_services[0].get("credentials", {})

    required = [
        "clientid",
        "clientsecret",
        "uri",
        "url",
    ]

    missing = [
        key for key in required
        if not credentials.get(key)
    ]

    if missing:
        raise RuntimeError(
            f"Destination Service credentials are missing: {missing}"
        )

    destination_service_url = credentials["uri"]
    xsuaa_url = credentials["url"]

    # Get XSUAA token to call Destination Service.
    token_response = requests.post(
        f"{xsuaa_url}/oauth/token",
        data={"grant_type": "client_credentials"},
        auth=(
            credentials["clientid"],
            credentials["clientsecret"],
        ),
        timeout=30,
    )
    token_response.raise_for_status()

    access_token = token_response.json()["access_token"]

    print("[DESTINATION] XSUAA access token obtained.")

    # Read the requested subaccount destination.
    destination_url = (
        f"{destination_service_url}"
        f"/destination-configuration/v2/destinations/"
        f"{destination_name}@subaccount"
    )

    response = requests.get(
        destination_url,
        headers={
            "Authorization": f"Bearer {access_token}",
        },
        timeout=30,
    )
    response.raise_for_status()

    destination = response.json().get(
        "destinationConfiguration"
    )

    if not destination:
        raise RuntimeError(
            f"Destination '{destination_name}' was not returned."
        )

    base_url = destination.get("URL")

    if not base_url:
        raise RuntimeError(
            f"Destination '{destination_name}' does not contain a URL."
        )

    credentials_result = {
        "base_url": base_url,
    }

    if api_key_property:
        api_key = destination.get(api_key_property)

        if not api_key:
            raise RuntimeError(
                f"API key was not found in destination property "
                f"'{api_key_property}'."
            )

        credentials_result["api_key"] = api_key

    print(f"[DESTINATION] URL: {base_url}")

    if api_key_property:
        print("[DESTINATION] API key loaded from destination.")

    return requests.Session(), credentials_result


def get_eia_http_client():
    return get_destination(
        EIA_DESTINATION_NAME,
        api_key_property="URL.queries.api_key",
    )


def get_fred_http_client():
    return get_destination(
        FRED_DESTINATION_NAME,
        api_key_property="URL.queries.api_key",
    )


def get_world_bank_http_client():
    return get_destination(
        WORLD_BANK_DESTINATION_NAME,
    )


# =============================================================================
# 10. EIA FETCH FUNCTIONS
# =============================================================================

def fetch_eia_series(
    http_client: requests.Session,
    api_key: str,
    base_url: str,
    route: str,
    series_id: str,
    column_name: str,
    frequency: str,
) -> pd.Series | None:
    """Fetch one EIA v2 series."""

    params: dict[str, Any] = {
        "api_key": api_key,
        "frequency": frequency,
        "data[0]": "value",
        "facets[series][]": series_id,
        "start": START_DATE,
        "sort[0][column]": "period",
        "sort[0][direction]": "asc",
        "length": (
            min(PAGE_SIZE, MAX_ROWS)
            if MAX_ROWS is not None
            else PAGE_SIZE
        ),
        "offset": 0,
    }

    if END_DATE is not None:
        params["end"] = END_DATE

    rows: list[dict[str, Any]] = []

    print(
        f"[EIA] Fetching {series_id} -> {column_name}"
    )

    url = f"{base_url}/{route}/data/"

    while True:
        response = http_client.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        data = (
            response.json()
            .get("response", {})
            .get("data", [])
        )

        if not data:
            break

        rows.extend(data)

        if (
            MAX_ROWS is not None
            and len(rows) >= MAX_ROWS
        ):
            rows = rows[:MAX_ROWS]
            break

        if len(data) < params["length"]:
            break

        params["offset"] += params["length"]
        time.sleep(REQUEST_DELAY_SECONDS)

    if not rows:
        print(
            f"[EIA] WARNING: No data returned for {series_id}"
        )
        return None

    frame = pd.DataFrame(rows)

    frame["period"] = pd.to_datetime(
        frame["period"],
        errors="coerce",
    )

    frame["value"] = pd.to_numeric(
        frame["value"],
        errors="coerce",
    )

    frame = frame.dropna(
        subset=["period", "value"]
    )

    series = (
        frame
        .drop_duplicates(subset=["period"])
        .sort_values("period")
        .set_index("period")["value"]
        .rename(column_name)
    )

    return series


def combine_eia_series(
    series_config: dict[str, str],
    route: str,
    frequency: str,
    dataset_name: str,
) -> pd.DataFrame:
    """Fetch and combine multiple EIA series into one DataFrame."""

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
        )

        if series is not None:
            series_frames.append(series)

        time.sleep(REQUEST_DELAY_SECONDS)

    if not series_frames:
        raise RuntimeError(
            f"EIA returned no {dataset_name} data."
        )

    result = (
        pd.concat(
            series_frames,
            axis=1,
            sort=True,
        )
        .sort_index()
        .reset_index()
    )

    result = limit_dataframe(
        result,
        MAX_ROWS,
    )

    print(
        f"[EIA] {dataset_name}: "
        f"{len(result)} rows"
    )

    return result


def fetch_eia_prices_daily() -> pd.DataFrame:
    """EIA daily crude/refined-product prices."""

    result = combine_eia_series(
        EIA_PRICE_SERIES,
        EIA_PRICES_ROUTE,
        "daily",
        "daily prices",
    )

    return round_numeric_columns(
        result,
        list(EIA_PRICE_SERIES.values()),
    )


def fetch_eia_inventory_weekly() -> pd.DataFrame:
    """EIA weekly petroleum inventories."""

    return combine_eia_series(
        EIA_INVENTORY_SERIES,
        EIA_INVENTORY_ROUTE,
        "weekly",
        "weekly inventory",
    )


def fetch_eia_refinery_util_weekly() -> pd.DataFrame:
    """EIA weekly refinery utilization."""

    return combine_eia_series(
        EIA_REFINERY_UTIL_SERIES,
        EIA_REFINERY_UTIL_ROUTE,
        "weekly",
        "refinery utilization",
    )


def fetch_eia_imports_exports_weekly() -> pd.DataFrame:
    """EIA weekly crude imports and exports."""

    return combine_eia_series(
        EIA_IMPORTS_EXPORTS_SERIES,
        EIA_IMPORTS_EXPORTS_ROUTE,
        "weekly",
        "imports/exports",
    )


# =============================================================================
# 11. FRED FETCH FUNCTIONS
# =============================================================================

def fetch_fred_series(
    http_client: requests.Session,
    api_key: str,
    base_url: str,
    series_id: str,
    column_name: str,
) -> pd.Series | None:
    """Fetch one FRED observation series."""

    params: dict[str, Any] = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": START_DATE,
    }

    if END_DATE is not None:
        params["observation_end"] = END_DATE

    print(
        f"[FRED] Fetching {series_id} -> {column_name}"
    )

    response = http_client.get(
        f"{base_url}{FRED_OBSERVATIONS_PATH}",
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    observations = response.json().get(
        "observations",
        [],
    )

    if not observations:
        print(
            f"[FRED] WARNING: No data returned for {series_id}"
        )
        return None

    frame = pd.DataFrame(observations)

    frame["date"] = pd.to_datetime(
        frame["date"],
        errors="coerce",
    )

    frame["value"] = pd.to_numeric(
        frame["value"],
        errors="coerce",
    )

    frame = frame.dropna(
        subset=["date", "value"]
    )

    return (
        frame
        .drop_duplicates(subset=["date"])
        .sort_values("date")
        .set_index("date")["value"]
        .rename(column_name)
    )


def combine_fred_series(
    series_config: dict[str, str],
    dataset_name: str,
) -> pd.DataFrame:
    """Fetch and combine multiple FRED series."""

    http_client, credentials = get_fred_http_client()

    frames: list[pd.Series] = []

    for series_id, column_name in series_config.items():
        series = fetch_fred_series(
            http_client=http_client,
            api_key=credentials["api_key"],
            base_url=credentials["base_url"],
            series_id=series_id,
            column_name=column_name,
        )

        if series is not None:
            frames.append(series)

        time.sleep(REQUEST_DELAY_SECONDS)

    if not frames:
        raise RuntimeError(
            f"FRED returned no {dataset_name} data."
        )

    result = (
        pd.concat(
            frames,
            axis=1,
            sort=True,
        )
        .sort_index()
        .reset_index()
        .rename(columns={"date": "period"})
    )

    result = limit_dataframe(
        result,
        MAX_ROWS,
    )

    print(
        f"[FRED] {dataset_name}: "
        f"{len(result)} rows"
    )

    return round_numeric_columns(
        result,
        list(series_config.values()),
    )


def fetch_fred_currency_daily() -> pd.DataFrame:
    """FRED daily currency indicators."""

    return combine_fred_series(
        FRED_CURRENCY_SERIES,
        "daily currency",
    )


def fetch_fred_inflation_monthly() -> pd.DataFrame:
    """FRED monthly inflation/macro indicators."""

    return combine_fred_series(
        FRED_INFLATION_SERIES,
        "monthly inflation/macro",
    )


# =============================================================================
# 12. YAHOO FINANCE FETCH
# =============================================================================

def fetch_yahoo_futures_daily() -> pd.DataFrame:
    """
    Fetch daily closing prices from Yahoo Finance.

    Yahoo is accessed directly; no SAP Destination is required.
    """

    print("[YAHOO] Fetching daily futures data...")

    data = yf.download(
        list(YAHOO_FUTURES.keys()),
        start=START_DATE,
        end=END_DATE,
        auto_adjust=False,
        progress=False,
        group_by="ticker",
    )

    if data.empty:
        raise RuntimeError(
            "Yahoo Finance returned no data."
        )

    series_frames: list[pd.Series] = []

    for ticker, column_name in YAHOO_FUTURES.items():
        try:
            close = data[ticker]["Close"].copy()
        except (KeyError, TypeError):
            print(
                f"[YAHOO] WARNING: No Close data for {ticker}"
            )
            continue

        close = pd.to_numeric(
            close,
            errors="coerce",
        )

        close.index = pd.to_datetime(
            close.index,
            errors="coerce",
        )

        close = close.dropna()
        close.name = column_name

        series_frames.append(close)

        print(
            f"[YAHOO] {ticker} -> {column_name}: "
            f"{len(close)} source records"
        )

    if not series_frames:
        raise RuntimeError(
            "Yahoo Finance returned no usable series."
        )

    result = (
        pd.concat(
            series_frames,
            axis=1,
            sort=True,
        )
        .sort_index()
        .reset_index()
        .rename(columns={"Date": "period"})
    )

    result = limit_dataframe(
        result,
        MAX_ROWS,
    )

    result = round_numeric_columns(
        result,
        list(YAHOO_FUTURES.values()),
    )

    print(
        f"[YAHOO] Final rows: {len(result)}"
    )

    return result


# =============================================================================
# 13. WORLD BANK FETCH
# =============================================================================

def fetch_world_bank_indicator(
    http_client: requests.Session,
    base_url: str,
    country: str,
    indicator: str,
) -> pd.Series | None:
    """Fetch one annual World Bank indicator."""

    url = (
        f"{base_url}/v2/country/"
        f"{country}/indicator/{indicator}"
    )

    params = {
        "format": "json",
        "per_page": 100,
    }

    print(
        f"[WORLD BANK] Fetching "
        f"{country} / {indicator}"
    )

    response = http_client.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    payload = response.json()

    if len(payload) < 2:
        print(
            f"[WORLD BANK] No data returned for "
            f"{country} / {indicator}"
        )
        return None

    rows = []

    for observation in payload[1]:
        year = observation.get("date")
        value = observation.get("value")

        if year is None or value is None:
            continue

        year_int = int(year)

        if year_int < WORLD_BANK_START_YEAR:
            continue

        if (
            WORLD_BANK_END_YEAR is not None
            and year_int > WORLD_BANK_END_YEAR
        ):
            continue

        rows.append(
            {
                "year": year_int,
                "value": float(value),
            }
        )

    if not rows:
        return None

    frame = pd.DataFrame(rows)

    return (
        frame
        .drop_duplicates(subset=["year"])
        .sort_values("year")
        .set_index("year")["value"]
    )


def fetch_worldbank_macro_annual() -> pd.DataFrame:
    """Fetch annual USA/world GDP growth and inflation."""

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

            column_name = (
                f"{country_name}_{indicator_name}"
            )

            datasets[column_name] = series.rename(
                column_name
            )

            time.sleep(REQUEST_DELAY_SECONDS)

    if not datasets:
        raise RuntimeError(
            "World Bank returned no data."
        )

    result = (
        pd.concat(
            datasets.values(),
            axis=1,
            sort=True,
        )
        .sort_index()
        .reset_index()
    )

    # World Bank has an annual YEAR key.
    result = limit_dataframe(
        result,
        WORLD_BANK_MAX_ROWS,
    )

    result = round_numeric_columns(
        result,
        list(datasets.keys()),
    )

    return result


def convert_worldbank_to_json_records(
    dataframe: pd.DataFrame,
) -> list[dict[str, Any]]:
    """
    Convert World Bank DataFrame to exact CDS field names.

    CDS:
        YEAR                         -> Integer
        USA_GDP_GROWTH_PCT           -> Decimal(15,6)
        USA_INFLATION_CPI_PCT        -> Decimal(15,6)
        WORLD_GDP_GROWTH_PCT         -> Decimal(15,6)
        WORLD_INFLATION_CPI_PCT      -> Decimal(15,6)
    """

    result = dataframe.copy()

    result = result.rename(
        columns={
            "year": "YEAR",
            "usa_gdp_growth": "USA_GDP_GROWTH_PCT",
            "usa_inflation": "USA_INFLATION_CPI_PCT",
            "world_gdp_growth": "WORLD_GDP_GROWTH_PCT",
            "world_inflation": "WORLD_INFLATION_CPI_PCT",
        }
    )

    decimal_columns = [
        "USA_GDP_GROWTH_PCT",
        "USA_INFLATION_CPI_PCT",
        "WORLD_GDP_GROWTH_PCT",
        "WORLD_INFLATION_CPI_PCT",
    ]

    # IMPORTANT:
    # Change DECIMAL_PLACES at the top of this file if CDS changes.
    result = round_numeric_columns(
        result,
        decimal_columns,
        DECIMAL_PLACES,
    )

    result = result.astype(object).where(
        pd.notna(result),
        None,
    )

    return result.to_dict(
        orient="records"
    )


# =============================================================================
# 14. CAP / XSUAA
# =============================================================================

def get_xsuaa_access_token() -> str:
    """Get a client-credentials OAuth token from the bound XSUAA service."""

    vcap_services = os.environ.get("VCAP_SERVICES")

    if not vcap_services:
        raise RuntimeError(
            "VCAP_SERVICES is not available. Run with cds bind."
        )

    services = json.loads(vcap_services)

    xsuaa_services = services.get("xsuaa", [])

    if not xsuaa_services:
        raise RuntimeError(
            "No XSUAA service binding was found."
        )

    credentials = xsuaa_services[0].get(
        "credentials",
        {},
    )

    required = [
        "url",
        "clientid",
        "clientsecret",
    ]

    missing = [
        key for key in required
        if not credentials.get(key)
    ]

    if missing:
        raise RuntimeError(
            f"XSUAA credentials are missing: {missing}"
        )

    token_response = requests.post(
        f"{credentials['url']}/oauth/token",
        data={
            "grant_type": "client_credentials",
        },
        auth=(
            credentials["clientid"],
            credentials["clientsecret"],
        ),
        timeout=30,
    )

    token_response.raise_for_status()

    token = token_response.json().get(
        "access_token"
    )

    if not token:
        raise RuntimeError(
            "XSUAA did not return an access token."
        )

    return token


def send_records_to_cap(
    records: list[dict[str, Any]],
    entity_name: str,
) -> None:
    """
    Send generic records to a CAP OData entity.

    The Python record keys are converted to uppercase because the
    current CDS entities expose uppercase field names.
    """

    if not records:
        print(
            f"[CAP] No records to send for {entity_name}."
        )
        return

    print(
        f"\n[CAP] Requesting XSUAA access token for "
        f"{entity_name}..."
    )

    access_token = get_xsuaa_access_token()

    url = (
        f"{CAP_SERVICE_URL}"
        f"{CAP_SERVICE_BASE_PATH}"
        f"/{entity_name}"
    )

    print(f"[CAP] Sending {entity_name} records...")
    print(f"[CAP] URL: {url}")
    print(f"[CAP] Record count: {len(records)}")

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": f"Bearer {access_token}",
    }

    successful = 0
    existing = 0
    failed = 0

    for record in records:

        cap_record = {
            key.upper(): value
            for key, value in record.items()
            if value is not None
        }

        response = requests.post(
            url,
            json=cap_record,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code in (200, 201):
            successful += 1

        elif response.status_code == 409:
            existing += 1

        else:
            failed += 1

            print(
                f"[CAP] ERROR for {entity_name}"
            )
            print(
                f"[CAP] Status: {response.status_code}"
            )
            print(
                f"[CAP] Response: {response.text}"
            )

    print(
        f"\n[CAP] {entity_name} upload completed."
    )
    print(f"[CAP] Successful : {successful}")
    print(f"[CAP] Existing   : {existing}")
    print(f"[CAP] Failed     : {failed}")

    if failed:
        raise RuntimeError(
            f"{failed} {entity_name} record(s) "
            "failed to upload to CAP."
        )


# =============================================================================
# 15. DATASET PROCESSORS
# =============================================================================

def process_eia() -> None:
    """Fetch and upload all four EIA datasets."""

    print("\n" + "=" * 70)
    print("EIA")
    print("=" * 70)

    datasets = [
        (
            "EIA_PRICES_DAILY",
            fetch_eia_prices_daily,
        ),
        (
            "EIA_INVENTORY_WEEKLY",
            fetch_eia_inventory_weekly,
        ),
        (
            "EIA_REFINERY_UTIL_WEEKLY",
            fetch_eia_refinery_util_weekly,
        ),
        (
            "EIA_IMPORTS_EXPORTS_WEEKLY",
            fetch_eia_imports_exports_weekly,
        ),
    ]

    for entity_name, fetch_function in datasets:
        print(f"\n[EIA] Processing {entity_name}...")

        dataframe = fetch_function()

        print(
            f"[EIA] {entity_name}: "
            f"{len(dataframe)} rows"
        )

        records = dataframe_to_json_records(
            dataframe
        )

        if records:
            print(
                f"[JSON] First {entity_name} record:"
            )
            print(records[0])

        send_records_to_cap(
            records,
            entity_name,
        )


def process_fred() -> None:
    """Fetch and upload both FRED datasets."""

    print("\n" + "=" * 70)
    print("FRED")
    print("=" * 70)

    datasets = [
        (
            "FRED_CURRENCY_DAILY",
            fetch_fred_currency_daily,
        ),
        (
            "FRED_INFLATION_MONTHLY",
            fetch_fred_inflation_monthly,
        ),
    ]

    for entity_name, fetch_function in datasets:
        print(
            f"\n[FRED] Processing {entity_name}..."
        )

        dataframe = fetch_function()

        records = dataframe_to_json_records(
            dataframe
        )

        if records:
            print(
                f"[JSON] First {entity_name} record:"
            )
            print(records[0])

        send_records_to_cap(
            records,
            entity_name,
        )


def process_yahoo() -> None:
    """Fetch and upload Yahoo Finance daily futures."""

    print("\n" + "=" * 70)
    print("YAHOO FINANCE")
    print("=" * 70)

    dataframe = fetch_yahoo_futures_daily()

    records = dataframe_to_json_records(
        dataframe
    )

    if records:
        print(
            "[JSON] First YAHOO_FUTURES_DAILY record:"
        )
        print(records[0])

    send_records_to_cap(
        records,
        "YAHOO_FUTURES_DAILY",
    )


def process_worldbank() -> None:
    """Fetch and upload World Bank annual macro data."""

    print("\n" + "=" * 70)
    print("WORLD BANK")
    print("=" * 70)

    dataframe = fetch_worldbank_macro_annual()

    print(
        f"[WORLD BANK] Final rows: "
        f"{len(dataframe)}"
    )

    records = convert_worldbank_to_json_records(
        dataframe
    )

    if records:
        print(
            "[JSON] First WORLDBANK_MACRO_ANNUAL record:"
        )
        print(records[0])

    send_records_to_cap(
        records,
        "WORLDBANK_MACRO_ANNUAL",
    )


# =============================================================================
# 16. MAIN - FETCH EVERYTHING IN ONE GO
# =============================================================================

def main() -> None:
    """
    Master execution.

    This is the function you use when you want to fetch:
        EIA + FRED + Yahoo + World Bank
    in one run.
    """

    validate_date_range()

    print("=" * 70)
    print("OIL MARKET DATA FETCH - ALL SOURCES")
    print("=" * 70)

    print("\n[CONFIG]")
    print(f"START_DATE              : {START_DATE}")
    print(f"END_DATE                : {END_DATE or 'LATEST'}")
    print(
        f"MAX_ROWS                : "
        f"{MAX_ROWS if MAX_ROWS is not None else 'ALL'}"
    )
    print(
        f"WORLD_BANK_START_YEAR  : "
        f"{WORLD_BANK_START_YEAR}"
    )
    print(
        f"WORLD_BANK_END_YEAR    : "
        f"{WORLD_BANK_END_YEAR or 'LATEST'}"
    )
    print(
        f"WORLD_BANK_MAX_ROWS    : "
        f"{WORLD_BANK_MAX_ROWS if WORLD_BANK_MAX_ROWS is not None else 'ALL'}"
    )
    print(
        f"DECIMAL_PLACES         : "
        f"{DECIMAL_PLACES}"
    )

    # -------------------------------------------------------------------------
    # EIA
    # -------------------------------------------------------------------------
    process_eia()

    # -------------------------------------------------------------------------
    # FRED
    # -------------------------------------------------------------------------
    process_fred()

    # -------------------------------------------------------------------------
    # Yahoo Finance
    # -------------------------------------------------------------------------
    process_yahoo()

    # -------------------------------------------------------------------------
    # World Bank
    # -------------------------------------------------------------------------
    process_worldbank()

    print("\n" + "=" * 70)
    print("ALL DATASETS COMPLETED")
    print("=" * 70)


if __name__ == "__main__":
    main()
