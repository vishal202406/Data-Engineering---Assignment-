"""R9 (business-day lead time), R10 (on-time), R11 (seller vs carrier lateness).

R9 convention (documented per the assignment's ask to be explicit about inclusive/exclusive):
we use numpy.busday_count(approval_date, delivery_date, holidays=...) semantics, i.e. the
START day is INCLUDED if it is itself a business day, and the END day (delivery) is EXCLUDED.
Same-day approval+delivery => 0 business days elapsed. This counts business days that fully
elapsed before the delivery, which is the usual meaning of "business-day lead time".

Regional holidays (Nager.Date 'counties' field, e.g. ["BR-SP"]) only count for an order whose
own customer_state (not the R1 aggregated home_state) matches that state — this is an order/
shipment-level property, see ASSUMPTIONS.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline.transform.dq import DQCollector


def compute_delivery_metrics(
    orders: pd.DataFrame,
    holidays: pd.DataFrame,
    dq: DQCollector,
) -> pd.DataFrame:
    """orders must have: order_id, customer_state, order_approved_at, order_purchase_timestamp,
    order_delivered_customer_date, order_estimated_delivery_date, is_revenue_eligible,
    has_broken_delivery. Returns orders with delivery_business_days and on_time added
    (NULL/None for rows where R3 applies or approval date is missing).
    """
    orders = orders.copy()
    national_holidays = set(pd.to_datetime(holidays.loc[holidays["state_code"] == "", "holiday_date"]))
    regional_by_state: dict[str, set] = {}
    for state, grp in holidays[holidays["state_code"] != ""].groupby("state_code"):
        regional_by_state[state] = set(pd.to_datetime(grp["holiday_date"]))

    approval_effective = orders["order_approved_at"].fillna(orders["order_purchase_timestamp"])

    biz_days = []
    on_time = []
    for idx, row in orders.iterrows():
        if row["has_broken_delivery"] or pd.isna(row["order_delivered_customer_date"]):
            biz_days.append(None)
            on_time.append(None)
            continue

        approved = approval_effective.loc[idx]
        delivered = row["order_delivered_customer_date"]

        if pd.isna(approved):
            biz_days.append(None)
            dq.log("R9", "order", row["order_id"], "missing_approval_date",
                   "order_approved_at and order_purchase_timestamp both missing; "
                   "cannot compute delivery_business_days.")
        else:
            state = row.get("customer_state")
            holiday_set = national_holidays | regional_by_state.get(state, set())
            holiday_arr = np.array(sorted(holiday_set), dtype="datetime64[D]") if holiday_set else np.array([], dtype="datetime64[D]")
            start = np.datetime64(approved.date(), "D")
            end = np.datetime64(delivered.date(), "D")
            if end < start:
                biz_days.append(None)
            else:
                biz_days.append(int(np.busday_count(start, end, holidays=holiday_arr)))

        if pd.isna(row["order_estimated_delivery_date"]):
            on_time.append(None)
        else:
            on_time.append(bool(delivered.date() <= row["order_estimated_delivery_date"].date()))

    orders["delivery_business_days"] = biz_days
    orders["on_time"] = on_time
    return orders


def compute_seller_lateness(order_items: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    """R11: per (order_id, seller_id), using the EARLIEST shipping_limit_date among that
    seller's items in the order. Returns one row per (order_id, seller_id) with lateness_status
    in {seller_late, carrier_late, on_time} or None where R3 (broken delivery) applies.
    """
    per_seller = (
        order_items.groupby(["order_id", "seller_id"])["shipping_limit_date"]
        .min()
        .reset_index()
        .rename(columns={"shipping_limit_date": "earliest_shipping_limit"})
    )
    merged = per_seller.merge(
        orders[["order_id", "order_delivered_carrier_date", "on_time", "has_broken_delivery"]],
        on="order_id", how="left",
    )

    def status(row):
        if row["has_broken_delivery"] or pd.isna(row["order_delivered_carrier_date"]):
            return None
        if row["order_delivered_carrier_date"] > row["earliest_shipping_limit"]:
            return "seller_late"
        if row["on_time"] is False:
            return "carrier_late"
        if row["on_time"] is True:
            return "on_time"
        return None

    merged["lateness_status"] = merged.apply(status, axis=1)
    return merged[["order_id", "seller_id", "earliest_shipping_limit", "lateness_status"]]
