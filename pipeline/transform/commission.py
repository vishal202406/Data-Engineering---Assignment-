"""R12 (seller tier) and R13 (commission rate), plus cleaning of the two messy internal
commission CSVs. The internal files were typed by humans: cleaning normalizes case/whitespace,
rejects impossible date ranges, and treats a missing effective_to as open-ended. Every rejected
or corrected row is logged (never silently dropped or defaulted).
"""
from __future__ import annotations

import pandas as pd

from pipeline.transform.dq import DQCollector

VALID_TIERS = {"bronze": "Bronze", "silver": "Silver", "gold": "Gold"}


def _parse_date_or_none(value) -> pd.Timestamp | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text == "":
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    return None if pd.isna(parsed) else parsed


def clean_commission_rates(raw: pd.DataFrame, dq: DQCollector) -> pd.DataFrame:
    rows = []
    for idx, row in raw.iterrows():
        row_id = f"commission_rates.csv:row{idx + 2}"  # +2: header + 1-indexed
        tier_raw = str(row["tier"]).strip()
        tier_key = tier_raw.lower()
        if tier_key not in VALID_TIERS:
            dq.log("R13", "internal_file_row", row_id, "rejected_unknown_tier",
                   f"Unrecognized tier value '{row['tier']}'.")
            continue
        tier = VALID_TIERS[tier_key]
        if tier_raw != tier:
            dq.log("R13", "internal_file_row", row_id, "corrected_tier_case",
                   f"Tier '{row['tier']}' normalized to '{tier}'.")

        try:
            rate = float(str(row["rate"]).strip())
        except (TypeError, ValueError):
            dq.log("R13", "internal_file_row", row_id, "rejected_invalid_rate",
                   f"Rate '{row['rate']}' is not numeric.")
            continue

        eff_from = _parse_date_or_none(row["effective_from"])
        if eff_from is None:
            dq.log("R13", "internal_file_row", row_id, "rejected_missing_effective_from",
                   "effective_from is required and missing/unparseable.")
            continue

        eff_to_raw = row.get("effective_to")
        eff_to = _parse_date_or_none(eff_to_raw)
        if eff_to is not None and str(eff_to_raw).strip() != "" and eff_to < eff_from:
            dq.log("R13", "internal_file_row", row_id, "rejected_impossible_range",
                   f"effective_to ({eff_to.date()}) is before effective_from ({eff_from.date()}).")
            continue

        rows.append({"tier": tier, "rate": rate, "effective_from": eff_from, "effective_to": eff_to})

    cleaned = pd.DataFrame(rows).sort_values(["tier", "effective_from"]).reset_index(drop=True)
    _log_overlaps(cleaned, "tier", "R13", dq, "commission_rates.csv")
    return cleaned


def clean_category_overrides(raw: pd.DataFrame, dq: DQCollector) -> pd.DataFrame:
    rows = []
    for idx, row in raw.iterrows():
        row_id = f"category_commission_overrides.csv:row{idx + 2}"
        category_raw = str(row["category_name_en"])
        category = category_raw.strip().lower()
        if category != category_raw:
            dq.log("R13", "internal_file_row", row_id, "corrected_category_whitespace_case",
                   f"Category '{category_raw}' normalized to '{category}'.")

        try:
            fixed_rate = float(str(row["fixed_rate"]).strip())
        except (TypeError, ValueError):
            dq.log("R13", "internal_file_row", row_id, "rejected_invalid_rate",
                   f"fixed_rate '{row['fixed_rate']}' is not numeric.")
            continue

        eff_from = _parse_date_or_none(row["effective_from"])
        if eff_from is None:
            dq.log("R13", "internal_file_row", row_id, "rejected_missing_effective_from",
                   "effective_from is required and missing/unparseable.")
            continue

        eff_to_raw = row.get("effective_to")
        eff_to = _parse_date_or_none(eff_to_raw)
        if eff_to is not None and str(eff_to_raw).strip() != "" and eff_to < eff_from:
            dq.log("R13", "internal_file_row", row_id, "rejected_impossible_range",
                   f"effective_to ({eff_to.date()}) is before effective_from ({eff_from.date()}); "
                   f"row for category '{category}' dropped.")
            continue

        notes = row.get("notes")
        rows.append({
            "category_name_en": category, "fixed_rate": fixed_rate,
            "effective_from": eff_from, "effective_to": eff_to,
            "notes": None if pd.isna(notes) else str(notes).strip() or None,
        })

    cleaned = pd.DataFrame(rows).sort_values(["category_name_en", "effective_from"]).reset_index(drop=True)
    _log_overlaps(cleaned, "category_name_en", "R13", dq, "category_commission_overrides.csv")
    return cleaned


def _log_overlaps(cleaned: pd.DataFrame, key_col: str, rule_id: str, dq: DQCollector, source: str) -> None:
    if cleaned.empty:
        return
    for key, group in cleaned.groupby(key_col):
        group = group.sort_values("effective_from")
        prev_to = None
        for _, row in group.iterrows():
            if prev_to is not None and (prev_to is None or row["effective_from"] <= prev_to):
                dq.log(rule_id, "internal_file_row", f"{source}:{key}", "overlapping_effective_range",
                       f"Overlapping effective date ranges found for '{key}' around {row['effective_from'].date()}.")
            prev_to = row["effective_to"] if row["effective_to"] is not None else pd.Timestamp.max


def compute_seller_monthly_revenue(order_items: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    """Revenue-eligible (R2: status == delivered) price total per seller per purchase month,
    computed from the FULL order history (not restricted to the run's --start/--end range),
    so that sub-range re-runs and the first month of any range still see the correct prior-month
    revenue for R12 tiering. See ASSUMPTIONS.md R12.
    """
    delivered_orders = orders[orders["order_status"] == "delivered"][["order_id", "order_purchase_timestamp"]]
    merged = order_items.merge(delivered_orders, on="order_id", how="inner")
    merged["year_month"] = pd.to_datetime(merged["order_purchase_timestamp"]).dt.to_period("M")
    revenue = merged.groupby(["seller_id", "year_month"])["price"].sum().reset_index()
    revenue.columns = ["seller_id", "year_month", "revenue_brl"]
    return revenue


def bucket_tier(revenue_brl: float) -> str:
    if revenue_brl > 20000:
        return "Gold"
    if revenue_brl >= 5000:
        return "Silver"
    return "Bronze"


def assign_seller_tier(items: pd.DataFrame, revenue: pd.DataFrame) -> pd.DataFrame:
    """Adds seller_tier_at_purchase to `items`, which must have seller_id and a
    `purchase_year_month` (pandas Period[M]) column. Tier(seller, M) depends on revenue(seller, M-1).
    """
    items = items.copy()
    items["_prev_month"] = items["purchase_year_month"] - 1
    lookup = revenue.rename(columns={"year_month": "_prev_month", "revenue_brl": "_prev_revenue"})
    items = items.merge(lookup, on=["seller_id", "_prev_month"], how="left")
    items["_prev_revenue"] = items["_prev_revenue"].fillna(0.0)
    items["seller_tier_at_purchase"] = items["_prev_revenue"].apply(bucket_tier)
    return items.drop(columns=["_prev_month", "_prev_revenue"])


def resolve_commission_rates(
    items: pd.DataFrame,
    commission_rates: pd.DataFrame,
    category_overrides: pd.DataFrame,
    dq: DQCollector,
) -> pd.DataFrame:
    """R13: category override (if active on purchase date) wins, else the tier rate active on
    the purchase date. `items` must have: order_id, order_item_id, category_name_en,
    seller_tier_at_purchase, purchase_date (date, not datetime).
    """
    items = items.copy().reset_index(drop=True)
    items["commission_rate"] = pd.NA

    # --- category overrides (merge_asof requires sorted frames) ---
    if not category_overrides.empty:
        items_sorted = items.sort_values("purchase_date").reset_index()
        overrides_sorted = category_overrides.sort_values("effective_from")
        matched = pd.merge_asof(
            items_sorted,
            overrides_sorted,
            left_on="purchase_date",
            right_on="effective_from",
            left_by="category_name_en",
            right_by="category_name_en",
            direction="backward",
        )
        valid = matched["effective_to"].isna() | (matched["purchase_date"].dt.normalize() <= matched["effective_to"])
        valid &= matched["fixed_rate"].notna()
        matched.loc[valid, "commission_rate"] = matched.loc[valid, "fixed_rate"]
        matched = matched.set_index("index")
        items.loc[matched.index, "commission_rate"] = matched["commission_rate"]

    # --- tier rate fallback, only where no override applied ---
    missing_mask = items["commission_rate"].isna()
    if missing_mask.any() and not commission_rates.empty:
        subset = items.loc[missing_mask].sort_values("purchase_date").reset_index()
        rates_sorted = commission_rates.rename(columns={"tier": "seller_tier_at_purchase"}).sort_values("effective_from")
        matched = pd.merge_asof(
            subset,
            rates_sorted,
            left_on="purchase_date",
            right_on="effective_from",
            left_by="seller_tier_at_purchase",
            right_by="seller_tier_at_purchase",
            direction="backward",
        )
        # effective_to is a calendar date and covers the WHOLE day; compare dates, not timestamps,
        # so a purchase late on the last covered day (e.g. 23:29 on effective_to) still matches.
        valid = matched["effective_to"].isna() | (matched["purchase_date"].dt.normalize() <= matched["effective_to"])
        valid &= matched["rate"].notna()
        matched.loc[valid, "commission_rate"] = matched.loc[valid, "rate"]
        matched = matched.set_index("index")
        items.loc[matched.index, "commission_rate"] = matched["commission_rate"]

    still_missing = items["commission_rate"].isna()
    for _, row in items[still_missing].iterrows():
        dq.log(
            "R13", "order_item", f"{row['order_id']}:{row['order_item_id']}", "no_commission_rate_found",
            f"No category override or tier rate active on {row['purchase_date']} "
            f"(category={row['category_name_en']}, tier={row['seller_tier_at_purchase']}).",
        )

    return items
