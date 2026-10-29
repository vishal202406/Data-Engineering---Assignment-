"""R18 — campaign attribution, evaluated per order item.

Window matching: date-only start/end are calendar dates in America/Sao_Paulo (end date
includes the full day, i.e. up to 23:59:59.999999). ISO timestamps ending in 'Z' are UTC and
are converted to America/Sao_Paulo before comparing against the (already-local) Olist
purchase timestamps.

Geo matching: a campaign matches on state if it declares target_states (ALL = any state);
it matches on region if it declares target_regions (ALL = any region). Either one satisfied
is enough (a campaign normally declares only one of the two).

Highest `priority` wins on multiple matches; ties broken by the later `start`.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import pandas as pd

SP_TZ = ZoneInfo("America/Sao_Paulo")


def _parse_boundary(value: str, date_format: str | None, is_end: bool) -> pd.Timestamp:
    if value.endswith("Z"):
        ts_utc = pd.Timestamp(value)
        return ts_utc.tz_convert(SP_TZ).tz_localize(None)

    if date_format == "DD/MM/YYYY":
        ts = pd.Timestamp(pd.to_datetime(value, format="%d/%m/%Y"))
    else:
        ts = pd.Timestamp(pd.to_datetime(value))

    if is_end:
        ts = ts + pd.DateOffset(days=1) - pd.Timedelta(microseconds=1)
    return ts


def normalize_campaigns(campaigns_raw: list[dict]) -> pd.DataFrame:
    rows = []
    for c in campaigns_raw:
        date_format = c.get("date_format")
        start = _parse_boundary(c["start"], date_format, is_end=False)
        end = _parse_boundary(c["end"], date_format, is_end=True)
        rows.append({
            "campaign_id": c["campaign_id"],
            "name": c["name"],
            "start_ts_sp": start,
            "end_ts_sp": end,
            "target_states": c.get("target_states"),
            "target_regions": c.get("target_regions"),
            "categories": c.get("categories", ["ALL"]),
            "priority": c["priority"],
        })
    return pd.DataFrame(rows)


def attribute_campaigns(items: pd.DataFrame, campaigns: pd.DataFrame) -> pd.Series:
    """items: purchase_ts_sp (naive, SP-local datetime), customer_state, customer_region,
    category_name_en. Returns a Series of campaign_id (or None) aligned to items.index.
    """
    best_campaign = pd.Series([None] * len(items), index=items.index, dtype=object)
    best_priority = pd.Series([-1] * len(items), index=items.index)
    best_start = pd.Series([pd.Timestamp.min] * len(items), index=items.index)

    for _, camp in campaigns.sort_values("start_ts_sp").iterrows():
        in_window = (items["purchase_ts_sp"] >= camp["start_ts_sp"]) & (items["purchase_ts_sp"] <= camp["end_ts_sp"])

        geo_match = pd.Series(False, index=items.index)
        if camp["target_states"] is not None:
            if "ALL" in camp["target_states"]:
                geo_match |= True
            else:
                geo_match |= items["customer_state"].isin(camp["target_states"])
        if camp["target_regions"] is not None:
            if "ALL" in camp["target_regions"]:
                geo_match |= True
            else:
                geo_match |= items["customer_region"].isin(camp["target_regions"])

        if "ALL" in camp["categories"]:
            category_match = pd.Series(True, index=items.index)
        else:
            category_match = items["category_name_en"].isin(camp["categories"])

        is_match = in_window & geo_match & category_match

        better = is_match & (
            (camp["priority"] > best_priority)
            | ((camp["priority"] == best_priority) & (camp["start_ts_sp"] > best_start))
        )
        best_campaign.loc[better] = camp["campaign_id"]
        best_priority.loc[better] = camp["priority"]
        best_start.loc[better] = camp["start_ts_sp"]

    return best_campaign
