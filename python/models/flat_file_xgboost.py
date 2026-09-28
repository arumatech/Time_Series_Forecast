import pandas as pd
from xgboost import XGBRegressor, DMatrix
from models.wti_data import align_wti
from math import cos, isfinite, pi, sin


def _annual_seasonality(dates):
    """Encode the forecasted date's annual position using calendar data only.

    Sine/cosine keep December and January adjacent. Scale leap years by 366
    days. No target prices or future market observations are used here.
    """
    dates = pd.DatetimeIndex(pd.to_datetime(dates, errors="raise"))
    if dates.isna().any():
        raise ValueError("Annual seasonality requires valid dates")
    angles = [
        2 * pi * (date.dayofyear - 1) / (366 if date.is_leap_year else 365)
        for date in dates
    ]
    return pd.DataFrame({
        "CAL_ANNUAL_SIN": [sin(angle) for angle in angles],
        "CAL_ANNUAL_COS": [cos(angle) for angle in angles],
    })


def _price_context(prices):
    """Inputs at each origin use only preceding observed prices.

    Windows count price observations, not calendar days. Require complete
    windows; never backfill early history from later observations.
    """
    prior = prices.shift(1)
    result = pd.DataFrame(index=prices.index)
    result["TARGET_LAG_1"] = prior
    result["TARGET_LAG_7"] = prices.shift(7)
    for window in (1, 5, 20, 60):
        result[f"TARGET_CHANGE_{window}_OBS"] = prior - prices.shift(window + 1)
    for window in (20, 60):
        result[f"TARGET_MEAN_{window}_OBS"] = prior.rolling(
            window, min_periods=window
        ).mean()
    # Controlled experiment: omit volatility from both training and prediction.
    # Keep all other price inputs, WTI inputs, and model settings unchanged.
    return result


def _new_model():
    # Keep tuning fixed while comparing price-context inputs.
    return XGBRegressor(
        n_estimators=80, max_depth=3, learning_rate=0.05,
        subsample=0.8, colsample_bytree=1.0,
        objective="reg:squarederror", random_state=42,
        n_jobs=1, tree_method="hist",
    )


def _log_validation(data, features, valid, labels, lead):
    """Evaluate a separate frozen model on recent historical forecast origins.

    The last 20 target observations define a shared validation boundary.
    Training labels must be strictly before that boundary, even when their
    input row is older. Validation inputs use history known at each origin.
    This is a rolling-origin evaluation, not one fixed 20-day forecast.
    """
    if len(data) < 20:
        print(f"FORECAST_DIAGNOSTIC lead={lead} status=skipped reason=short_history", flush=True)
        return
    cutoff = data["PRICEDATE"].iloc[-20]
    label_dates = pd.Series(
        pd.DatetimeIndex(data["PRICEDATE"]) + pd.offsets.BDay(lead - 1),
        index=data.index,
    )
    train = valid & (data["PRICEDATE"] < cutoff) & (label_dates < cutoff)
    validation = valid & (data["PRICEDATE"] >= cutoff)
    n_train, n_validation = int(train.sum()), int(validation.sum())
    if n_train < 30 or n_validation < 5:
        print(f"FORECAST_DIAGNOSTIC lead={lead} status=skipped "
              f"reason=insufficient_split_rows train_rows={n_train} "
              f"validation_rows={n_validation} cutoff={cutoff.date()}", flush=True)
        return
    diagnostic_model = _new_model()
    # Each origin has its own last known price. Learn total movement from
    # that price to the horizon's actual price, not a one-day increment.
    changes = labels - data["TARGET_LAG_1"]
    diagnostic_model.fit(data.loc[train, features], changes.loc[train])

    def mae(actual, predicted):
        actual = actual.to_numpy(dtype=float)
        predicted = list(predicted)
        if len(actual) != len(predicted) or not all(isfinite(float(v)) for v in predicted):
            raise ValueError("Invalid diagnostic predictions")
        return sum(abs(float(a) - float(p)) for a, p in zip(actual, predicted)) / len(actual)

    training_prices = (data.loc[train, "TARGET_LAG_1"].to_numpy(dtype=float)
                       + diagnostic_model.predict(data.loc[train, features]))
    validation_prices = (data.loc[validation, "TARGET_LAG_1"].to_numpy(dtype=float)
                         + diagnostic_model.predict(data.loc[validation, features]))
    training_mae = mae(labels.loc[train], training_prices)
    validation_mae = mae(labels.loc[validation], validation_prices)
    baseline_mae = mae(labels.loc[validation], data.loc[validation, "TARGET_LAG_1"])
    print(f"FORECAST_DIAGNOSTIC lead={lead} status=ok "
          f"cutoff={cutoff.date()} train_rows={n_train} validation_rows={n_validation} "
          f"training_label_end={label_dates.loc[train].max().date()} "
          f"validation_origin_start={data.loc[validation, 'PRICEDATE'].min().date()} "
          f"validation_origin_end={data.loc[validation, 'PRICEDATE'].max().date()} "
          f"training_MAE={training_mae:.6f} validation_MAE={validation_mae:.6f} "
          f"baseline_MAE={baseline_mae:.6f} "
          f"beats_baseline={validation_mae < baseline_mae} "
          f"evaluation=rolling_origins_frozen_model target=price_change_from_origin error_units=price", flush=True)


def _log_prediction_contributions(model, future, training_inputs, prediction, lead, date):
    """Explain the fitted model's prediction; never modify or refit it.

    Exact Tree SHAP contributions sum with the model-specific bias to the
    predicted price change for reg:squarederror. Adding the origin's latest
    price reconstructs the saved price forecast. They describe model attribution,
    not causal effects or the effect of removing a factor and retraining.
    Each horizon has its own model and therefore its own reference value.
    """
    booster = model.get_booster()
    values = booster.predict(
        DMatrix(future), pred_contribs=True, approx_contribs=False,
        validate_features=True,
    )
    if values.shape != (1, len(future.columns) + 1):
        raise ValueError(f"Unexpected contribution shape: {values.shape}")
    contributions = [float(value) for value in values[0, :-1]]
    bias = float(values[0, -1])
    latest = float(future["TARGET_LAG_1"].iloc[0])
    reconstructed_change = bias + sum(contributions)
    reconstructed = latest + reconstructed_change
    if not all(isfinite(value) for value in contributions + [bias, reconstructed]):
        raise ValueError("Nonfinite prediction contributions")
    if abs(reconstructed - prediction) > 1e-5 * max(1.0, abs(prediction)):
        raise ValueError("Contribution sum does not match saved-model prediction")
    pairs = list(zip(future.columns, contributions))
    wti_total = sum(value for name, value in pairs if name.startswith("WTI_"))
    season_total = sum(value for name, value in pairs if name.startswith("CAL_"))
    price_total = sum(value for name, value in pairs
                      if not name.startswith(("WTI_", "CAL_")))
    print(f"PREDICTION_EXPLANATION version=change_contributions_v1 status=ok "
          f"lead={lead} date={date.date()} prediction={prediction:.8f} "
          f"model_reference_change={bias:+.8f} price_contribution={price_total:+.8f} "
          f"wti_contribution={wti_total:+.8f} seasonality_contribution={season_total:+.8f} "
          f"reconstructed={reconstructed:.8f} "
          f"latest_price={latest:.8f} predicted_change={prediction-latest:+.8f} "
          f"reconstructed_change={reconstructed_change:+.8f} target=price_change_from_origin "
          f"interpretation=model_attribution_not_causation", flush=True)
    # Largest absolute effects first; log every input, including zero effects.
    for name, contribution in sorted(pairs, key=lambda pair: abs(pair[1]), reverse=True):
        value = float(future[name].iloc[0])
        low, high = float(training_inputs[name].min()), float(training_inputs[name].max())
        direction = "UP" if contribution > 0 else "DOWN" if contribution < 0 else "NEUTRAL"
        print(f"PREDICTION_CONTRIBUTION lead={lead} date={date.date()} "
              f"feature={name} input_value={value:.8f} contribution={contribution:+.8f} "
              f"direction={direction} training_min={low:.8f} training_max={high:.8f} "
              f"outside_training_range={value < low or value > high}", flush=True)


def _direct_wti_forecast(data, features, horizon, start, wti_history, lag):
    """Fit one model per weekday lead; never feed predictions into inputs.

    Each historical row is a forecast origin. Its inputs precede that origin,
    and its learning target is the actual price at the requested weekday lead
    minus the last price known at that historical origin. Every horizon's
    predicted total change is added to the same current origin price.
    Missing weekday labels (including holidays) are skipped, never filled.
    """
    if isinstance(horizon, bool) or int(horizon) != horizon or horizon <= 0:
        raise ValueError("horizon must be a positive integer")
    horizon = int(horizon)
    last_date = data["PRICEDATE"].iloc[-1]
    start = (pd.Timestamp(start).normalize() if start is not None
             else last_date.normalize() + pd.Timedelta(days=1))
    if not 1 <= (start - last_date).days <= 7:
        raise ValueError("WTI forecast requires target history within 7 days before forecast start")

    output_dates = pd.date_range(start, periods=horizon, freq="D")
    pricing_dates = [date for date in output_dates if date.dayofweek < 5]
    prices = data["PRICE"].tolist()
    # For weekend job starts, the first new prediction is Monday. WTI is
    # still frozen at the original job start, not read from that Monday.
    wti = None
    if wti_history is not None:
        wti = align_wti_momentum([start], wti_history, lag)
        if wti.isna().any().any():
            raise ValueError("WTI level/change history missing or stale at forecast origin")
    # Append an unknown target so training and prediction share exactly the
    # same shifted calculation. No future actual or prediction enters inputs.
    origin_prices = pd.Series(prices + [float("nan")], dtype=float)
    future = _price_context(origin_prices).iloc[[-1]].reset_index(drop=True)
    if wti is not None:
        for column in wti.columns:
            future[column] = float(wti[column].iloc[0])
    future = future.loc[:, features]
    if not all(isfinite(float(value)) for value in future.iloc[0]):
        raise ValueError(
            "Price context requires at least 61 finite historical price observations "
            "before forecast start, plus complete factor inputs"
        )
    print(f"PRICE_CONTEXT version=price_change_target_v1 features={','.join(features)} "
          f"origin_values={future.iloc[0].to_dict()}", flush=True)

    actuals = data.set_index("PRICEDATE")["PRICE"]
    plans = []
    for lead, date in enumerate(pricing_dates, start=1):
        # Date-based lookup avoids treating a missing holiday quote as an
        # extra observed trading day. All labels come from cutoff data only.
        label_dates = pd.DatetimeIndex(data["PRICEDATE"]) + pd.offsets.BDay(lead - 1)
        labels = pd.Series(actuals.reindex(label_dates).to_numpy(), index=data.index)
        valid = data[features].notna().all(axis=1) & labels.notna()
        count = int(valid.sum())
        if count < 30:
            raise ValueError(
                f"Direct weekday lead {lead} has only {count} complete training rows; need 30"
            )
        plans.append((lead, date, valid, labels, count))

    predictions = {}
    for lead, date, valid, labels, count in plans:
        # Each direct model predicts a different target date. Use that date's
        # calendar position in both training and prediction, not the same
        # origin-date seasonality for every horizon. Calendar dates are known
        # in advance; price and WTI inputs remain frozen at the origin.
        label_dates = pd.DatetimeIndex(data["PRICEDATE"]) + pd.offsets.BDay(lead - 1)
        seasonal_inputs = _annual_seasonality(label_dates)
        seasonal_future = _annual_seasonality([date])
        lead_data = data.copy()
        lead_future = future.copy()
        lead_features = list(features) + list(seasonal_inputs.columns)
        for column in seasonal_inputs.columns:
            lead_data[column] = seasonal_inputs[column].to_numpy()
            lead_future[column] = float(seasonal_future[column].iloc[0])
        lead_future = lead_future.loc[:, lead_features]
        print(f"FORECAST_SEASONALITY version=annual_calendar_v1 lead={lead} "
              f"date={date.date()} features={','.join(seasonal_inputs.columns)} "
              f"values={seasonal_future.iloc[0].to_dict()}", flush=True)
        model = _new_model()
        changes = labels - data["TARGET_LAG_1"]
        model.fit(lead_data.loc[valid, lead_features], changes.loc[valid])
        predicted_change = float(model.predict(lead_future)[0])
        prediction = float(future["TARGET_LAG_1"].iloc[0]) + predicted_change
        if not isfinite(prediction):
            raise ValueError(f"Nonfinite direct forecast at weekday lead {lead}")
        predictions[date] = prediction
        print(f"DIRECT_XGBOOST lead={lead} date={date.date()} training_rows={count} "
              f"prediction={prediction:.4f} predicted_change={predicted_change:+.8f} "
              f"target=price_change_from_origin", flush=True)
        if wti is not None:
            try:
                _log_prediction_contributions(
                    model, lead_future, lead_data.loc[valid, lead_features], prediction, lead, date
                )
            except Exception as exc:
                # Optional explanation must not prevent saving a valid forecast.
                print(f"PREDICTION_EXPLANATION lead={lead} date={date.date()} "
                      f"status=error error={type(exc).__name__}: {exc}", flush=True)
        # Separate diagnostic fit: never replace the production model or its
        # prediction. An unavailable diagnostic must be visible in the logs.
        try:
            _log_validation(lead_data, lead_features, valid, labels, lead)
        except Exception as exc:
            print(f"FORECAST_DIAGNOSTIC lead={lead} status=error "
                  f"error={type(exc).__name__}: {exc}", flush=True)

    print(f"FORECAST_FACTORS={'WTI' if wti is not None else 'NONE'} feature_set=long_context_annual_seasonality_v1 "
          f"strategy=direct_per_weekday target=price_change_from_origin version=price_change_target_v1 models={len(plans)} "
          f"seasonality=annual_calendar_v1 "
          f"history_end={last_date.date()} forecast_start={start.date()} "
          f"wti_at_origin={wti['WTI_ASOF'].iloc[0] if wti is not None else 'NA'} "
          f"assumed_publication_lag_days={lag} "
          f"forecast_calendar=weekdays weekend_output=carry_forward "
          f"holiday_calendar=not_configured", flush=True)
    forecasts = []
    displayed_price = float(prices[-1])
    for date in output_dates:
        if date in predictions:
            displayed_price = predictions[date]
        forecasts.append({"date": date.strftime("%Y-%m-%d"),
                          "predicted_value": round(displayed_price, 4)})
    return forecasts


def align_wti_momentum(dates, history, publication_lag_days):
    """Align WTI levels and recent changes using the existing availability rule.

    Changes are absolute USD/barrel differences over available observations,
    not calendar days. Absolute differences also support zero/negative WTI.
    """
    # Validate the source and align levels with the existing helper first.
    levels = align_wti(dates, history, publication_lag_days)
    observed = history[["PERIOD", "WTI_CRUDE_USD_BBL"]].copy()
    observed["PERIOD"] = pd.to_datetime(observed["PERIOD"], errors="raise")
    observed["WTI_CRUDE_USD_BBL"] = pd.to_numeric(
        observed["WTI_CRUDE_USD_BBL"], errors="raise"
    ).astype(float)
    observed = observed.dropna(subset=["WTI_CRUDE_USD_BBL"]).sort_values("PERIOD")
    result = pd.DataFrame({"WTI_ASOF": levels})
    for lag in (1, 5):
        changes = observed.copy()
        changes["WTI_CRUDE_USD_BBL"] = observed["WTI_CRUDE_USD_BBL"].diff(lag)
        if changes["WTI_CRUDE_USD_BBL"].notna().any():
            result[f"WTI_CHANGE_{lag}_OBS"] = align_wti(
                dates, changes, publication_lag_days
            )
        else:
            result[f"WTI_CHANGE_{lag}_OBS"] = float("nan")
    return result


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
# Flat-file XGBoost forecast with optional WTI and longer price context
# ============================================================

def train_and_forecast_flat_file(
    df,
    dcsid,
    mic,
    pricetype=None,
    horizon=30,
    wti_history=None,
    forecast_start_date=None,
    wti_publication_lag_days=None,
    direct_forecast=False,
):

    # A3/GG uses the same direct strategy for both menu selections.
    # Other price-only instruments retain their existing route.
    if direct_forecast or wti_history is not None:
        if (str(dcsid).strip(), str(mic).strip()) != ("A3", "GG"):
            raise ValueError("WTI connection is currently mapped only to A3/GG")
        if forecast_start_date is not None:
            forecast_start_date = pd.Timestamp(forecast_start_date).normalize()
            df = df.copy()
            df["PRICEDATE"] = pd.to_datetime(df["PRICEDATE"], errors="raise")
            df = df[df["PRICEDATE"] < forecast_start_date]

    data = prepare_target_data(
        df,
        dcsid,
        mic,
        pricetype
    )

    if (direct_forecast or wti_history is not None) and data["PRICEDATE"].dt.dayofweek.ge(5).any():
        raise ValueError(
            "A3/GG weekday calendar found weekend observations in target history; "
            "confirm the publication calendar before forecasting"
        )

    # --------------------------------------------------------
    # Create target lag features
    # --------------------------------------------------------

    data["TARGET_LAG_1"] = data["PRICE"].shift(1)
    data["TARGET_LAG_7"] = data["PRICE"].shift(7)

    features = ["TARGET_LAG_1", "TARGET_LAG_7"]
    if direct_forecast or wti_history is not None:
        if "PRICETYPE" not in data or not data["PRICETYPE"].eq("CL").all():
            raise ValueError("WTI connection requires A3/GG price type CL")
        original_dates = df.loc[
            df["DCSID"].astype(str).str.strip().eq(str(dcsid).strip()) &
            df["MIC"].astype(str).str.strip().eq(str(mic).strip())
        ].copy()
        if pricetype is not None:
            original_dates = original_dates[original_dates["PRICETYPE"].eq(pricetype)]
        if pd.to_datetime(original_dates["PRICEDATE"]).duplicated().any():
            raise ValueError("Resolve duplicate target dates/maturities before enabling WTI")
        # Apply the same availability assumption independently at each historical
        # target date. No same-day WTI observation enters that day's prediction.
        # Lag the gasoline changes: today's actual price is the training label,
        # so it must never enter today's input features.
        price_features = _price_context(data["PRICE"])
        features = list(price_features.columns)
        for column in features:
            data[column] = price_features[column]
        if wti_history is not None:
            wti_features = align_wti_momentum(
                data["PRICEDATE"].tolist(), wti_history, wti_publication_lag_days
            )
            for column in wti_features.columns:
                data[column] = wti_features[column].to_numpy()
                features.append(column)

        return _direct_wti_forecast(
            data, features, horizon, forecast_start_date,
            wti_history, wti_publication_lag_days,
        )

    # Preserve the existing price-only route for unmapped instruments.
    data = data.dropna(subset=["PRICE"] + features).reset_index(drop=True)
    model = _new_model()
    model.fit(data[features], data["PRICE"])
    history = data["PRICE"].tolist()
    last_date = data["PRICEDATE"].iloc[-1]
    forecasts = []
    for i in range(horizon):
        future = pd.DataFrame([[history[-1], history[-7]]], columns=features)
        prediction = float(model.predict(future)[0])
        history.append(prediction)
        date = last_date + pd.Timedelta(days=i + 1)
        forecasts.append({"date": date.strftime("%Y-%m-%d"),
                          "predicted_value": round(prediction, 4)})
    return forecasts
