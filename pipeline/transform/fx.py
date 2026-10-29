"""R6 — currency conversion. Rate date = date of order_approved_at (fallback: purchase
timestamp if approved_at is NULL). If no rate exists for that exact date (weekends/holidays),
use the most recent EARLIER available rate, never a later one.
"""
from __future__ import annotations

import pandas as pd

from pipeline.transform.dq import DQCollector


def attach_fx_rate(df: pd.DataFrame, fx_rates: pd.DataFrame, date_col: str, dq: DQCollector, entity_type: str, id_cols: list[str]) -> pd.DataFrame:
    """Adds fx_rate and fx_rate_date columns to df, looked up from fx_rates (rate_date, brl_to_usd)
    using "most recent earlier-or-equal" matching. df[date_col] must be a date (not datetime).
    """
    df = df.copy().reset_index(drop=True)
    fx_sorted = fx_rates.sort_values("rate_date").reset_index(drop=True)
    fx_sorted["rate_date"] = pd.to_datetime(fx_sorted["rate_date"])

    lookup_dates = pd.to_datetime(df[date_col])
    valid_mask = lookup_dates.notna()

    result_rate = pd.Series([pd.NA] * len(df), dtype="object")
    result_date = pd.Series([pd.NaT] * len(df), dtype="datetime64[ns]")

    if valid_mask.any():
        tmp = pd.DataFrame({"_lookup_date": lookup_dates[valid_mask]}).reset_index()
        matched = pd.merge_asof(
            tmp.sort_values("_lookup_date"),
            fx_sorted,
            left_on="_lookup_date",
            right_on="rate_date",
            direction="backward",
        ).set_index("index")
        result_rate.loc[matched.index] = matched["brl_to_usd"]
        result_date.loc[matched.index] = matched["rate_date"]

    df["fx_rate"] = pd.to_numeric(result_rate, errors="coerce")
    df["fx_rate_date"] = result_date

    missing = df["fx_rate"].isna()
    if missing.any():
        for _, row in df[missing].iterrows():
            entity_id = ":".join(str(row[c]) for c in id_cols)
            dq.log("R6", entity_type, entity_id, "no_fx_rate_available",
                   f"No BRL->USD rate on or before {row[date_col]}.")

    return df
