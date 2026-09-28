"""Read WTI without modifying source tables; align by a configurable delay.

PERIOD is not a publication timestamp. Delay-based availability is a pilot
assumption and does not reconstruct historical revisions or late releases.
"""
import pandas as pd


def read_wti_history(connection_factory, start_date, forecast_start_date):
    start, cutoff = pd.Timestamp(start_date), pd.Timestamp(forecast_start_date)
    if pd.isna(start) or pd.isna(cutoff) or start >= cutoff:
        raise ValueError("Invalid WTI history date range")
    connection = connection_factory()
    try:
        cursor = connection.cursor()
        try:
            cursor.execute(
                'SELECT "PERIOD", "WTI_CRUDE_USD_BBL" '
                'FROM "ZFOR_EIA_PRICES_DAILY" '
                'WHERE "PERIOD" >= ? AND "PERIOD" < ? ORDER BY "PERIOD"',
                (start.date(), cutoff.date()),
            )
            data = pd.DataFrame(cursor.fetchall(), columns=["PERIOD", "WTI_CRUDE_USD_BBL"])
        finally:
            cursor.close()
    finally:
        connection.close()
    if data.empty:
        raise ValueError("No WTI history found in the selected HANA schema/date range")
    return data


def align_wti(dates, history, publication_lag_days):
    """Most recent nonmissing WTI estimated available by each forecast date.

    Values older than 14 calendar days are unavailable, not carried forever.
    Never fill from a later observation. Retain zero and negative market prices.
    """
    if isinstance(publication_lag_days, bool) or not isinstance(publication_lag_days, int) or publication_lag_days < 1:
        raise ValueError("WTI publication lag must be a positive integer")
    data = history[["PERIOD", "WTI_CRUDE_USD_BBL"]].copy()
    data["PERIOD"] = pd.to_datetime(data["PERIOD"], errors="raise")
    if data["PERIOD"].isna().any() or data["PERIOD"].duplicated().any():
        raise ValueError("WTI history contains missing or duplicate dates")
    data["WTI_CRUDE_USD_BBL"] = pd.to_numeric(data["WTI_CRUDE_USD_BBL"], errors="raise").astype(float)
    if data["WTI_CRUDE_USD_BBL"].isin([float("inf"), float("-inf")]).any():
        raise ValueError("WTI contains infinite values")
    data = data.dropna(subset=["WTI_CRUDE_USD_BBL"]).sort_values("PERIOD")
    if data.empty:
        raise ValueError("WTI has no usable prices")
    data["AVAILABLE"] = data["PERIOD"] + pd.Timedelta(days=publication_lag_days)
    left = pd.DataFrame({"DATE":pd.to_datetime(dates), "POSITION":range(len(dates))})
    left["DATE"] = left["DATE"].astype("datetime64[ns]")
    data["AVAILABLE"] = data["AVAILABLE"].astype("datetime64[ns]")
    joined = pd.merge_asof(left.sort_values("DATE"), data, left_on="DATE", right_on="AVAILABLE", direction="backward")
    age = (joined["DATE"] - joined["PERIOD"]).dt.days
    joined.loc[age.gt(14), "WTI_CRUDE_USD_BBL"] = float("nan")
    return joined.sort_values("POSITION")["WTI_CRUDE_USD_BBL"].to_numpy()
