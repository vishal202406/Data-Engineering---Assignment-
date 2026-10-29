"""R2 (revenue-eligible orders), R3 (broken delivered orders), R5 (payment reconciliation)."""
from __future__ import annotations

import pandas as pd

from pipeline.transform.dq import DQCollector

EXCLUDED_STATUSES = {"canceled", "unavailable"}


def classify_orders(orders: pd.DataFrame, dq: DQCollector) -> pd.DataFrame:
    """Drops canceled/unavailable orders entirely (R2: excluded from everything except the DQ
    report, which we log here). Adds is_revenue_eligible and has_broken_delivery to the rest.
    """
    excluded = orders[orders["order_status"].isin(EXCLUDED_STATUSES)]
    for status, grp in excluded.groupby("order_status"):
        dq.log_many("R2", "order", grp["order_id"], "excluded_order_status",
                     f"Order status '{status}' excluded from revenue/commission/delivery per R2.")

    kept = orders[~orders["order_status"].isin(EXCLUDED_STATUSES)].copy()
    kept["is_revenue_eligible"] = (kept["order_status"] == "delivered").astype(int)
    kept["has_broken_delivery"] = (
        (kept["order_status"] == "delivered") & kept["order_delivered_customer_date"].isna()
    )

    broken = kept[kept["has_broken_delivery"]]
    dq.log_many("R3", "order", broken["order_id"], "broken_delivered_order",
                "Status delivered but order_delivered_customer_date is NULL; "
                "revenue-eligible but excluded from delivery/SLA metrics.")

    return kept


def reconcile_payments(order_items: pd.DataFrame, payments: pd.DataFrame, dq: DQCollector) -> pd.DataFrame:
    """Per order: net_cash_paid_brl (payments excluding vouchers) vs item gross; flags
    payment_mismatch when |payment_total - item_gross_total| > 1.00 BRL. Does not drop orders.
    """
    item_gross = order_items.groupby("order_id")["item_gross_brl"].sum().rename("item_gross_total")

    payment_total = payments.groupby("order_id")["payment_value"].sum().rename("payment_total_brl")
    non_voucher = payments[payments["payment_type"] != "voucher"]
    net_cash = non_voucher.groupby("order_id")["payment_value"].sum().rename("net_cash_paid_brl")

    result = pd.concat([item_gross, payment_total, net_cash], axis=1).reset_index()
    result["payment_total_brl"] = result["payment_total_brl"].fillna(0.0)
    result["net_cash_paid_brl"] = result["net_cash_paid_brl"].fillna(0.0)
    result["item_gross_total"] = result["item_gross_total"].fillna(0.0)

    result["payment_mismatch"] = (
        (result["payment_total_brl"] - result["item_gross_total"]).abs() > 1.00
    )

    mismatched = result[result["payment_mismatch"]]
    for _, row in mismatched.iterrows():
        dq.log("R5", "order", row["order_id"], "payment_mismatch",
               f"payment_total={row['payment_total_brl']:.2f} vs item_gross={row['item_gross_total']:.2f}.")

    return result[["order_id", "net_cash_paid_brl", "payment_total_brl", "payment_mismatch"]]
