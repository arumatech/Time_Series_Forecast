"""
Time Series Forecast - External Data Fetcher

Fetches forecasting features from:
    - EIA API       : crude/refined prices, inventory, refinery utilization,
                      crude imports/exports
    - FRED API      : currency, inflation/macro indicators
    - Yahoo Finance : futures and dollar index
    - World Bank API: GDP growth and inflation

IMPORTANT:
    - No .pkl files are created.
    - No .csv files are created.
    - Data is kept in pandas DataFrames in memory.
    - The DataFrames can later be converted to JSON records and sent
      from Python -> CAP -> SAP HANA.

Environment variables required:
    EIA_KEY
    FRED_KEY

Examples:
    python fetch_data.py
    python fetch_data.py eia
    python fetch_data.py fred
    python fetch_data.py yahoo
    python fetch_data.py worldbank
"""

import os
import sys
import time
import requests
import pandas as pd

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# FRED_KEY = os.environ.get("FRED_KEY")
# EIA_KEY = os.environ.get("EIA_KEY")

# START = "2005-01-01"


FRED_KEY = os.environ.get("FRED_KEY")
EIA_KEY = os.environ.get("EIA_KEY")

# ---------------------------------------------------------------------------
# CAP / HANA
# ---------------------------------------------------------------------------

CAP_URL = "http://localhost:4004"
CAP_EIA_PRICE_URL = f"{CAP_URL}/odata/v4/forecast/EIA_PRICE"

START = "2005-01-01"

session = requests.Session()
session.headers.update({
    "User-Agent": "time-series-forecast-fetcher/1.0"
})


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_keys():
    """Validate API keys before calling EIA/FRED."""
    missing = []

    if not EIA_KEY:
        missing.append("EIA_KEY")

    if not FRED_KEY:
        missing.append("FRED_KEY")

    if missing:
        raise RuntimeError(
            "Missing environment variable(s): "
            + ", ".join(missing)
            + "\n"
            "Set them before running the script."
        )


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def dataframe_to_records(df: pd.DataFrame):
    """
    Convert a DataFrame into JSON-serializable records.

    This helper will be used later when sending data to CAP.
    For now, it simply prepares the in-memory data.
    """
    if df is None or df.empty:
        return []

    result = df.reset_index().copy()

    # Convert timestamps to ISO strings.
    for col in result.columns:
        if pd.api.types.is_datetime64_any_dtype(result[col]):
            result[col] = result[col].dt.strftime("%Y-%m-%d")

    # Replace NaN / NaT with None for JSON compatibility.
    result = result.astype(object).where(pd.notnull(result), None)

    return result.to_dict(orient="records")


# ---------------------------------------------------------------------------
# CAP / HANA upload
# ---------------------------------------------------------------------------

def send_eia_price_to_cap(date, wti_crude, brent_crude):
    """
    Send one EIA price record to the CAP OData service.

    This is a small test function for the first Python -> CAP -> HANA flow.
    """

    payload = {
        "DATE": date,
        "WTI_CRUDE": wti_crude,
        "BRENT_CRUDE": brent_crude,
    }

    response = session.post(
        CAP_EIA_PRICE_URL,
        json=payload,
        timeout=60,
    )

    response.raise_for_status()

    print("Successfully sent record to CAP:")
    print(response.json())

    return response.json()


def print_summary(name: str, df: pd.DataFrame):
    """Print a compact summary of a fetched dataset."""
    print("\n" + "=" * 80)
    print(f"{name}")
    print("=" * 80)

    if df is None or df.empty:
        print("No data returned.")
        return

    print(f"Rows       : {len(df):,}")
    print(f"Columns    : {df.shape[1]:,}")
    print(f"Date range : {df.index.min()} -> {df.index.max()}")
    print("\nColumns:")
    for col in df.columns:
        print(f"  - {col}")

    print("\nSample:")
    print(df.head(3).to_string())


# ---------------------------------------------------------------------------
# EIA v2
# ---------------------------------------------------------------------------

def eia_series(route, facet_id_field, series_ids, freq, label_map):
    """
    Fetch multiple EIA v2 series and return a wide DataFrame.

    The returned DataFrame is kept in memory.
    No CSV or pickle file is created.
    """

    if not EIA_KEY:
        raise RuntimeError("EIA_KEY is not set.")

    frames = []

    for sid in series_ids:
        url = f"https://api.eia.gov/v2/{route}/data/"

        params = {
            "api_key": EIA_KEY,
            "frequency": freq,
            "data[0]": "value",
            f"facets[{facet_id_field}][]": sid,
            "start": START,
            "sort[0][column]": "period",
            "sort[0][direction]": "asc",
            "length": 5000,
            "offset": 0,
        }

        rows = []

        while True:
            response = session.get(
                url,
                params=params,
                timeout=60
            )
            response.raise_for_status()

            payload = response.json()
            data = payload.get("response", {}).get("data", [])

            if not data:
                break

            rows.extend(data)

            if len(data) < params["length"]:
                break

            params["offset"] += params["length"]
            time.sleep(0.2)

        if not rows:
            print(f"    WARNING: no EIA data returned for {sid}")
            continue

        frame = pd.DataFrame(rows)[["period", "value"]].copy()

        frame["period"] = pd.to_datetime(
            frame["period"],
            errors="coerce"
        )

        frame["value"] = pd.to_numeric(
            frame["value"],
            errors="coerce"
        )

        frame = frame.dropna(subset=["period", "value"])

        series = (
            frame
            .drop_duplicates(subset=["period"])
            .set_index("period")["value"]
        )

        series.name = label_map.get(sid, sid)

        frames.append(series)

        time.sleep(0.2)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, axis=1).sort_index()


def fetch_eia():
    """Fetch all EIA datasets."""

    validate_keys()

    datasets = {}

    # -----------------------------------------------------------------------
    # EIA daily spot prices
    # -----------------------------------------------------------------------

    print("\n[EIA] Crude + refined spot prices (daily)")

    spot_ids = {
        "RWTC": "wti_crude_usd_bbl",
        "RBRTE": "brent_crude_usd_bbl",
        "EER_EPMRR_PF4_Y05LA_DPG": "la_rbob_carbob_usd_gal",
        "EER_EPMRU_PF4_RGC_DPG": "gulf_gasoline_usd_gal",
        "EER_EPMRU_PF4_Y35NY_DPG": "nyh_gasoline_usd_gal",
        "EER_EPD2DXL0_PF4_RGC_DPG": "gulf_ulsd_diesel_usd_gal",
        "EER_EPJK_PF4_RGC_DPG": "gulf_jetfuel_usd_gal",
    }

    prices = eia_series(
        "petroleum/pri/spt",
        "series",
        list(spot_ids),
        "daily",
        spot_ids
    )

    datasets["eia_prices_daily"] = prices
    print_summary("EIA - Daily Prices", prices)

    # -----------------------------------------------------------------------
    # EIA weekly inventory
    # -----------------------------------------------------------------------

    print("\n[EIA] Crude inventory / stocks (weekly)")

    stock_ids = {
        "WCESTUS1": "crude_stocks_excl_spr_kbbl",
        "WCSSTUS1": "crude_stocks_spr_kbbl",
        "WGTSTUS1": "total_gasoline_stocks_kbbl",
        "WD0ST_NUS_1": "distillate_stocks_kbbl",
    }

    stocks = eia_series(
        "petroleum/stoc/wstk",
        "series",
        list(stock_ids),
        "weekly",
        stock_ids
    )

    datasets["eia_inventory_weekly"] = stocks
    print_summary("EIA - Weekly Inventory", stocks)

    # -----------------------------------------------------------------------
    # EIA refinery utilization
    # -----------------------------------------------------------------------

    print("\n[EIA] Refinery utilization (weekly)")

    util_ids = {
        "WPULEUS3": "refinery_utilization_pct"
    }

    utilization = eia_series(
        "petroleum/pnp/wiup",
        "series",
        list(util_ids),
        "weekly",
        util_ids
    )

    datasets["eia_refinery_util_weekly"] = utilization
    print_summary("EIA - Refinery Utilization", utilization)

    # -----------------------------------------------------------------------
    # EIA imports / exports
    # -----------------------------------------------------------------------

    print("\n[EIA] Crude imports / exports (weekly)")

    move_ids = {
        "WCEIMUS2": "crude_imports_kbbl_d",
        "WCREXUS2": "crude_exports_kbbl_d",
    }

    movements = eia_series(
        "petroleum/move/wkly",
        "series",
        list(move_ids),
        "weekly",
        move_ids
    )

    datasets["eia_imports_exports_weekly"] = movements
    print_summary("EIA - Imports / Exports", movements)

    return datasets


# ---------------------------------------------------------------------------
# FRED
# ---------------------------------------------------------------------------

def fred_series(series_map):
    """
    Fetch multiple FRED series and return a wide DataFrame.
    """

    if not FRED_KEY:
        raise RuntimeError("FRED_KEY is not set.")

    frames = []

    for sid, label in series_map.items():

        url = "https://api.stlouisfed.org/fred/series/observations"

        params = {
            "series_id": sid,
            "api_key": FRED_KEY,
            "file_type": "json",
            "observation_start": START,
        }

        response = session.get(
            url,
            params=params,
            timeout=60
        )

        response.raise_for_status()

        observations = response.json().get("observations", [])

        if not observations:
            print(f"    WARNING: no FRED data returned for {sid}")
            continue

        frame = pd.DataFrame(observations)[
            ["date", "value"]
        ].copy()

        frame["date"] = pd.to_datetime(
            frame["date"],
            errors="coerce"
        )

        frame["value"] = pd.to_numeric(
            frame["value"],
            errors="coerce"
        )

        frame = frame.dropna(
            subset=["date", "value"]
        )

        series = (
            frame
            .drop_duplicates(subset=["date"])
            .set_index("date")["value"]
        )

        series.name = label

        frames.append(series)

        time.sleep(0.15)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, axis=1).sort_index()


def fetch_fred():
    """Fetch all FRED datasets."""

    validate_keys()

    datasets = {}

    # -----------------------------------------------------------------------
    # FRED currency
    # -----------------------------------------------------------------------

    print("\n[FRED] Currency")

    currency_series = {
        "DTWEXBGS": "usd_broad_index",
        "DEXUSEU": "usd_per_eur",
        "DEXCHUS": "cny_per_usd",
        "DEXJPUS": "jpy_per_usd",
    }

    currency = fred_series(currency_series)

    datasets["fred_currency_daily"] = currency
    print_summary("FRED - Currency", currency)

    # -----------------------------------------------------------------------
    # FRED inflation / macro
    # -----------------------------------------------------------------------

    print("\n[FRED] Inflation / macro")

    macro_series = {
        "CPIAUCSL": "cpi_all_urban",
        "CPIENGSL": "cpi_energy",
        "PPIACO": "ppi_all_commodities",
        "FEDFUNDS": "fed_funds_rate",
    }

    macro = fred_series(macro_series)

    datasets["fred_inflation_monthly"] = macro
    print_summary("FRED - Inflation / Macro", macro)

    return datasets


# ---------------------------------------------------------------------------
# Yahoo Finance
# ---------------------------------------------------------------------------

def fetch_yahoo():
    """Fetch futures and dollar index from Yahoo Finance."""

    print("\n[Yahoo] Futures + dollar index (daily)")

    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError(
            "yfinance is not installed. "
            "Run: pip install yfinance"
        ) from exc

    tickers = {
        "CL=F": "wti_future",
        "BZ=F": "brent_future",
        "RB=F": "rbob_gasoline_future",
        "HO=F": "heating_oil_future",
        "NG=F": "natgas_future",
        "DX-Y.NYB": "dollar_index",
    }

    raw = yf.download(
        list(tickers),
        start=START,
        auto_adjust=False,
        progress=False,
        group_by="ticker"
    )

    frames = []

    for ticker, label in tickers.items():
        try:
            series = raw[ticker]["Close"].rename(label)
            frames.append(series)
        except Exception as exc:
            print(f"    WARNING {ticker}: {exc}")

    if not frames:
        return pd.DataFrame()

    futures = pd.concat(
        frames,
        axis=1
    ).sort_index()

    futures.index.name = "date"

    print_summary(
        "Yahoo Finance - Futures",
        futures
    )

    return {
        "yahoo_futures_daily": futures
    }


# ---------------------------------------------------------------------------
# World Bank
# ---------------------------------------------------------------------------

def fetch_worldbank():
    """Fetch annual GDP growth and inflation from World Bank."""

    print("\n[WorldBank] GDP growth + inflation (annual)")

    indicators = {
        "NY.GDP.MKTP.KD.ZG": "gdp_growth_pct",
        "FP.CPI.TOTL.ZG": "inflation_cpi_pct",
    }

    countries = {
        "USA": "usa",
        "WLD": "world",
    }

    frames = []

    for country_code, country_name in countries.items():

        for indicator_code, label in indicators.items():

            url = (
                "https://api.worldbank.org/v2/"
                f"country/{country_code}/"
                f"indicator/{indicator_code}"
            )

            try:
                response = session.get(
                    url,
                    params={
                        "format": "json",
                        "per_page": 500
                    },
                    timeout=60
                )

                response.raise_for_status()

                payload = response.json()

            except Exception as exc:
                print(
                    f"    WARNING {country_code}/{label}: {exc}"
                )
                continue

            if (
                len(payload) < 2
                or payload[1] is None
            ):
                print(
                    f"    WARNING {country_code}/{label}: "
                    "empty payload"
                )
                continue

            records = [
                (int(item["date"]), item["value"])
                for item in payload[1]
                if item["value"] is not None
            ]

            series = pd.Series(
                dict(records)
            ).sort_index()

            series.name = (
                f"{country_name}_{label}"
            )

            frames.append(series)

            time.sleep(0.15)

    if not frames:
        return {
            "worldbank_macro_annual": pd.DataFrame()
        }

    worldbank = pd.concat(
        frames,
        axis=1
    ).sort_index()

    worldbank.index.name = "year"

    print_summary(
        "World Bank - Annual Macro",
        worldbank
    )

    return {
        "worldbank_macro_annual": worldbank
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_all():
    """
    Fetch all supported data sources.

    Returns:
        dict[str, pandas.DataFrame]
    """

    datasets = {}

    datasets.update(fetch_eia())
    datasets.update(fetch_fred())
    datasets.update(fetch_yahoo())
    datasets.update(fetch_worldbank())

    return datasets


def main():
    """
    Command-line entry point.

    Supported:
        all
        eia
        fred
        yahoo
        worldbank
    """

    which = (
        sys.argv[1].lower()
        if len(sys.argv) > 1
        else "all"
    )

    print("=" * 80)
    print("TIME SERIES FORECAST - DATA FETCHER")
    print("=" * 80)
    print(f"Start date : {START}")
    print(f"Mode       : {which}")
    print("Output     : IN MEMORY ONLY")
    print("Files      : NO PKL / NO CSV")
    print("=" * 80)

    if which == "all":
        datasets = fetch_all()

    elif which == "eia":
        datasets = fetch_eia()

    elif which == "fred":
        datasets = fetch_fred()

    elif which == "yahoo":
        datasets = fetch_yahoo()

    elif which == "worldbank":
        datasets = fetch_worldbank()

    else:
        raise ValueError(
            "Invalid option. Use: "
            "all, eia, fred, yahoo, or worldbank"
        )

    print("\n" + "=" * 80)
    print("FETCH COMPLETE")
    print("=" * 80)

    total_rows = 0

    for name, df in datasets.items():
        rows = len(df) if df is not None else 0
        total_rows += rows
        print(f"{name:35s} {rows:>10,} rows")

    print("-" * 80)
    print(f"{'TOTAL':35s} {total_rows:>10,} rows")
    print("=" * 80)

    # Example for the next phase:
    #
    # records = dataframe_to_records(
    #     datasets["eia_prices_daily"]
    # )
    #
    # These records will later be sent to CAP:
    #
    # POST /odata/v4/forecast/ingest
    #
    # We intentionally do NOT send anything to CAP yet.


if __name__ == "__main__":
    main()
