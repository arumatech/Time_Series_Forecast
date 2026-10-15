"""Download real EIA WTI observations via FRED and exercise the flat-file ARIMA model.

Writes local CSV/JSON files only. Does not connect to HANA or start the job worker.
"""

import argparse
from datetime import date, datetime, timezone
from io import StringIO
import json
from math import isfinite
from pathlib import Path

import pandas as pd
import requests

from models.flat_file_arima import train_and_forecast_flat_file_arima


SOURCE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SERIES_ID = "DCOILWTICO"
DCSID, MIC, PRICE_TYPE = "EIA_WTI", "TEST", "SP"


def prepare_flatfile(source, start, end):
    """Map published observations to the existing upload schema, without filling gaps."""
    date_column = "observation_date" if "observation_date" in source else "DATE"
    if not {date_column, SERIES_ID}.issubset(source.columns):
        raise ValueError("Expected a FRED WTI CSV with a date and DCOILWTICO column")
    dates = pd.to_datetime(source[date_column], errors="raise")
    prices = pd.to_numeric(source[SERIES_ID].replace(".", None), errors="raise")
    frame = pd.DataFrame({"PRICEDATE": dates, "PRICE": prices})
    frame = frame.dropna(subset=["PRICEDATE", "PRICE"])
    frame = frame[frame["PRICEDATE"].between(pd.Timestamp(start), pd.Timestamp(end))]
    frame = frame.sort_values("PRICEDATE").reset_index(drop=True)
    if len(frame) < 60:
        raise ValueError("Choose a date range with at least 60 real observations")
    if frame["PRICEDATE"].duplicated().any():
        raise ValueError("Source contains duplicate dates")
    if not frame["PRICE"].map(isfinite).all():
        raise ValueError("Source contains non-finite prices")
    frame["DCSID"], frame["MIC"], frame["PRICETYPE"] = DCSID, MIC, PRICE_TYPE
    # Spot prices have no contract maturity; this is an explicit test sentinel.
    frame["MKEYDT"] = "2099-12-31"
    frame["PER"], frame["UOM"], frame["CURRENCY"] = 1, "BBL", "USD"
    frame["PRICEDATE"] = frame["PRICEDATE"].dt.strftime("%Y-%m-%d")
    return frame[["DCSID", "MIC", "PRICETYPE", "MKEYDT", "PRICEDATE",
                  "PRICE", "PER", "UOM", "CURRENCY"]]


def forecast(frame, start, horizon):
    return pd.DataFrame(train_and_forecast_flat_file_arima(
        frame, DCSID, MIC, PRICE_TYPE, horizon, start,
    )).rename(columns={"date": "PRICEDATE", "predicted_value": "ARIMA_PRICE"})


def evaluate(frame, horizon):
    """Five chronological holdouts; score only dates with published actuals."""
    actuals = frame[["PRICEDATE", "PRICE"]]
    dates = pd.to_datetime(frame["PRICEDATE"])
    end = dates.iloc[-1]
    starts = []
    for _ in range(5):
        # End each nonoverlapping holdout on a real observation. A one-day
        # horizon must not accidentally select an empty weekend/holiday.
        start = end - pd.Timedelta(days=horizon - 1)
        starts.append(start)
        previous = dates[dates < start]
        if len(previous) < 30:
            raise ValueError("Need more history for five holdouts at this horizon")
        end = previous.iloc[-1]
    scored = []
    for start in reversed(starts):
        training = frame[frame["PRICEDATE"] < start.strftime("%Y-%m-%d")]
        if len(training) < 30:
            raise ValueError("Need more history for five holdouts at this horizon")
        predictions = forecast(training, start, horizon)
        matched = predictions.merge(actuals, on="PRICEDATE", how="inner")
        if matched.empty:
            raise ValueError("A holdout has no published actuals; choose a longer horizon")
        matched["BASELINE_PRICE"] = float(training["PRICE"].iloc[-1])
        matched["FORECAST_START"] = start.strftime("%Y-%m-%d")
        scored.append(matched)
    results = pd.concat(scored, ignore_index=True)
    arima_error = results["ARIMA_PRICE"] - results["PRICE"]
    baseline_error = results["BASELINE_PRICE"] - results["PRICE"]
    return results, {
        "holdouts": len(starts),
        "scored_actual_observations": len(results),
        "arima_order": [5, 1, 0],
        "horizon_calendar_days": horizon,
        "arima_mae_usd_bbl": float(arima_error.abs().mean()),
        "baseline_mae_usd_bbl": float(baseline_error.abs().mean()),
        "arima_rmse_usd_bbl": float((arima_error.pow(2).mean()) ** 0.5),
        "baseline_rmse_usd_bbl": float((baseline_error.pow(2).mean()) ** 0.5),
        "arima_beats_baseline_mae": bool(arima_error.abs().mean() < baseline_error.abs().mean()),
        "note": "Small historical comparison, not a guarantee. Current revised data; calendar-day model with forward-filled training gaps. Only published actuals are scored.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--forecast-start", type=date.fromisoformat)
    parser.add_argument("--horizon", type=int, choices=range(1, 31), default=7, metavar="1..30")
    parser.add_argument("--source-csv", type=Path, help="Reuse a downloaded FRED CSV offline")
    parser.add_argument("--output-dir", type=Path, default=Path("samples/eia-wti"))
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("--start must be on or before --end")
    try:
        if args.source_csv:
            source_text = args.source_csv.read_text(encoding="utf-8-sig")
            retrieved_at = None  # A local file does not establish its download time.
        else:
            response = requests.get(SOURCE_URL, params={
                "id": SERIES_ID, "cosd": args.start.isoformat(), "coed": args.end.isoformat(),
            }, timeout=30)
            response.raise_for_status()
            source_text = response.text
            retrieved_at = datetime.now(timezone.utc).isoformat()
        frame = prepare_flatfile(pd.read_csv(StringIO(source_text)), args.start, args.end)
        latest = pd.Timestamp(frame["PRICEDATE"].iloc[-1])
        forecast_start = pd.Timestamp(args.forecast_start) if args.forecast_start else latest + pd.Timedelta(days=1)
        if forecast_start <= latest:
            raise ValueError("--forecast-start must follow the last source observation; shorten --end for a historical run")
        predictions = forecast(frame, forecast_start, args.horizon)
        holdouts, metrics = evaluate(frame, args.horizon)
        provenance = {
            "publisher": "U.S. Energy Information Administration",
            "distributor": "FRED, Federal Reserve Bank of St. Louis",
            "series_id": SERIES_ID,
            "series_url": "https://fred.stlouisfed.org/series/DCOILWTICO",
            "download_url": SOURCE_URL,
            "retrieved_at_utc": retrieved_at,
            "requested_start": args.start.isoformat(), "requested_end": args.end.isoformat(),
            "observations": len(frame),
            "first_observation": frame["PRICEDATE"].iloc[0],
            "last_observation": frame["PRICEDATE"].iloc[-1],
            "forecast_start": forecast_start.strftime("%Y-%m-%d"),
            "test_mapping": {"DCSID": DCSID, "MIC": MIC, "PRICETYPE": PRICE_TYPE,
                             "MKEYDT": "2099-12-31", "PER": 1, "UOM": "BBL", "CURRENCY": "USD"},
            "mapping_note": "Identifiers, price-type label and maturity sentinel are test conventions, not EIA instrument metadata. Prices are unmodified published USD/barrel spot observations.",
        }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "source.csv").write_text(source_text, encoding="utf-8")
        frame.to_csv(args.output_dir / "flatfile.csv", index=False)
        predictions.to_csv(args.output_dir / "forecast.csv", index=False)
        holdouts.to_csv(args.output_dir / "backtest.csv", index=False)
        for name, value in [("provenance.json", provenance), ("evaluation.json", metrics)]:
            (args.output_dir / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"output_dir": str(args.output_dir.resolve()), **provenance, "evaluation": metrics}, indent=2))
    except (ValueError, OSError, requests.RequestException) as exc:
        parser.exit(1, f"Flat-file demo failed: {exc}\n")


if __name__ == "__main__":
    main()
