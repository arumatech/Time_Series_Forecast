"""Daily EIA -> existing CAP/HANA prices -> evaluated oil-price forecast."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urljoin, urlsplit
from uuid import uuid4

import numpy as np
import pandas as pd
from statsmodels.tsa.arima.model import ARIMA


BENCHMARKS = {"WTI": "WTI_CRUDE_USD_BBL", "BRENT": "BRENT_CRUDE_USD_BBL"}
MIN_TRAIN = 60
BACKTEST_ORIGINS = 5
HISTORY_LIMIT = 1260
INTERVAL_LEVEL = 0.8


def prepare_history(records, benchmark, as_of):
    """Keep actual observations; reject ambiguous or invalid target history."""
    column = BENCHMARKS[benchmark]
    frame = pd.DataFrame(records)
    if frame.empty or not {"PERIOD", column}.issubset(frame.columns):
        raise ValueError(f"No {benchmark} history; load historical EIA prices first")
    frame["PERIOD"] = pd.to_datetime(frame["PERIOD"], errors="raise")
    frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame = frame.dropna(subset=[column]).sort_values("PERIOD")
    if frame.empty or frame["PERIOD"].isna().any():
        raise ValueError("Oil history has missing dates or no usable prices")
    if frame["PERIOD"].duplicated().any():
        raise ValueError("Oil history contains duplicate observation dates")
    if not np.isfinite(frame[column].to_numpy(dtype=float)).all():
        raise ValueError("Oil history contains nonfinite prices")
    if (frame["PERIOD"] > pd.Timestamp(as_of)).any():
        raise ValueError("Oil history contains future observations")
    # Preserve gaps and zero/negative prices; do not invent historical actuals.
    return frame.set_index("PERIOD")[column].astype(float)


def predict(values, steps, model):
    values = np.asarray(values, dtype=float)
    if model == "NAIVE":
        mean = np.repeat(values[-1], steps)
        # Approximate 80% random-walk bands, requiring independent normal changes.
        spread = 1.2815515655446004 * np.std(np.diff(values), ddof=1)
        spread *= np.sqrt(np.arange(1, steps + 1))
        return mean, mean - spread, mean + spread
    fitted = ARIMA(values, order=(1, 1, 0)).fit()
    if not fitted.mle_retvals.get("converged", True):
        raise ValueError("ARIMA did not converge")
    result = fitted.get_forecast(steps=steps)
    bounds = np.asarray(result.conf_int(alpha=1 - INTERVAL_LEVEL))
    mean = np.asarray(result.predicted_mean)
    if not np.isfinite(np.concatenate([mean, bounds.ravel()])).all():
        raise ValueError("ARIMA returned nonfinite forecasts")
    return mean, bounds[:, 0], bounds[:, 1]


def evaluate_models(history, horizon, leads=None):
    """Rolling origins, with only earlier observations entering each fit."""
    required = MIN_TRAIN + horizon + BACKTEST_ORIGINS - 1
    if len(history) < required:
        raise ValueError(f"Need at least {required} actual observations for evaluation")
    values = history.to_numpy()
    errors = {"NAIVE": [], "ARIMA": []}
    arima_ok = True
    for origin in range(len(values) - horizon - BACKTEST_ORIGINS + 1,
                        len(values) - horizon + 1):
        train, actual = values[:origin], values[origin:origin + horizon]
        errors["NAIVE"].append(np.abs(actual - train[-1]))
        if arima_ok:
            try:
                mean, _, _ = predict(train, horizon, "ARIMA")
                errors["ARIMA"].append(np.abs(actual - mean))
            except (ValueError, np.linalg.LinAlgError) as exc:
                print(f"[MODEL] ARIMA evaluation failed; using baseline: {exc}")
                arima_ok = False
    naive_mae = np.mean(errors["NAIVE"], axis=0)
    arima_mae = np.mean(errors["ARIMA"], axis=0) if arima_ok else None
    positions = np.asarray(leads or range(1, horizon + 1)) - 1
    model = "ARIMA" if arima_ok and arima_mae[positions].mean() < naive_mae[positions].mean() else "NAIVE"
    return model, naive_mae, arima_mae


def build_forecast(history, benchmark, horizon=5, issued_at=None, max_age_days=7):
    if benchmark not in BENCHMARKS or not 1 <= horizon <= 30:
        raise ValueError("Choose WTI/BRENT and a horizon between 1 and 30")
    issued_at = issued_at or datetime.now(timezone.utc)
    today = issued_at.astimezone(timezone.utc).date()
    cutoff = history.index[-1].date()
    if cutoff > today or (today - cutoff).days > max_age_days:
        raise ValueError(f"Oil data cutoff {cutoff} is future-dated or stale")
    targets = pd.bdate_range(today + timedelta(days=1), periods=horizon)
    # Bridge the publication gap so today's forecast is not mislabeled as
    # a one-step forecast from an older EIA observation. Weekdays approximate
    # the publication calendar; exchange-specific holidays are not configured.
    leads = [len(pd.bdate_range(cutoff + timedelta(days=1), target)) for target in targets]
    model, naive_mae, arima_mae = evaluate_models(history, leads[-1], leads)
    try:
        mean, lower, upper = predict(history.to_numpy(), leads[-1], model)
    except (ValueError, np.linalg.LinAlgError) as exc:
        if model != "ARIMA":
            raise
        print(f"[MODEL] ARIMA fit failed; using baseline: {exc}")
        model = "NAIVE"
        mean, lower, upper = predict(history.to_numpy(), leads[-1], model)
    forecasts = []
    for index, (target, lead) in enumerate(zip(targets, leads)):
        forecasts.append({
            "TARGET_DATE": target.date().isoformat(),
            "HORIZON_STEP": index + 1,
            "OBSERVATION_LEAD": lead,
            "PRICE_USD_BBL": round(float(mean[lead - 1]), 4),
            "LOWER_USD_BBL": round(float(lower[lead - 1]), 4),
            "UPPER_USD_BBL": round(float(upper[lead - 1]), 4),
            "NAIVE_MAE": round(float(naive_mae[lead - 1]), 4),
            "ARIMA_MAE": round(float(arima_mae[lead - 1]), 4) if arima_mae is not None else None,
        })
    return {
        "ID": str(uuid4()),
        "BENCHMARK": benchmark,
        "MODEL": model,
        "MODEL_VERSION": "oil-univariate-v1",
        "DATA_CUTOFF": cutoff.isoformat(),
        "ISSUED_AT": issued_at.astimezone(timezone.utc).isoformat(),
        "HORIZON": horizon,
        "INTERVAL_LEVEL": INTERVAL_LEVEL,
        "FORECASTS": forecasts,
    }


def read_history(updater, benchmark, as_of):
    column = BENCHMARKS[benchmark]
    query = urlencode({
        "$select": f"PERIOD,{column}",
        "$filter": f"{column} ne null and PERIOD le {as_of}",
        "$orderby": "PERIOD desc",
        "$top": HISTORY_LIMIT,
    }, quote_via=quote)
    url = f"{updater.cap_entity_url('EIA_PRICES_DAILY')}?{query}"
    origin = urlsplit(url)
    headers = updater.get_cap_headers()
    records = []
    with updater.requests.Session() as session:
        while url:
            response = updater.request_with_retry(
                session, "GET", url, headers=headers,
                operation=f"Read {benchmark} history",
            )
            payload = response.json()
            records.extend(payload["value"])
            next_link = payload.get("@odata.nextLink")
            url = urljoin(url, next_link) if next_link else None
            if url and (urlsplit(url).scheme, urlsplit(url).netloc) != (origin.scheme, origin.netloc):
                raise ValueError("CAP pagination changed the service origin")
    return prepare_history(records, benchmark, as_of)


def run_pipeline(updater, benchmark="WTI", horizon=5, overlap_days=30):
    updater.OVERLAP_DAYS = overlap_days
    update = updater.run_dataset(
        "EIA_PRICES_DAILY", updater.fetch_eia_prices_daily,
        key_field="PERIOD", frequency="daily",
    )
    if update["status"] not in {"SUCCESS", "UP_TO_DATE"}:
        raise RuntimeError(f"EIA update failed; forecast skipped: {update.get('error')}")
    issued_at = datetime.now(timezone.utc)
    history = read_history(updater, benchmark, issued_at.date().isoformat())
    run = build_forecast(history, benchmark, horizon, issued_at)
    # A composition saves the parent and its forecast rows in one transaction.
    # PUT with a stable run ID is also safe if a transient error triggers retry.
    with updater.requests.Session() as session:
        updater.request_with_retry(
            session, "PUT", f"{updater.cap_entity_url('OIL_FORECAST_RUNS')}({run['ID']})",
            headers=updater.get_cap_headers(), json_body=run,
            expected_statuses={200, 201, 204}, operation="Save oil forecast run",
        )
    print(json.dumps({"status": "SUCCESS", "run_id": run["ID"],
                      "benchmark": benchmark, "model": run["MODEL"],
                      "data_cutoff": run["DATA_CUTOFF"], "forecast_rows": horizon}))
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", choices=BENCHMARKS, default="WTI")
    parser.add_argument("--horizon", type=int, choices=range(1, 31), default=5, metavar="1..30")
    parser.add_argument("--overlap-days", type=int, default=30)
    args = parser.parse_args()
    if args.overlap_days < 1:
        parser.error("--overlap-days must be positive")
    # Import after argument parsing: --help works without BTP bindings.
    import update_oil_data as updater
    run_pipeline(updater, args.benchmark, args.horizon, args.overlap_days)


if __name__ == "__main__":
    main()
