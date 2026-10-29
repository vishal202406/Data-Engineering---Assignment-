"""R1 — customer identity. customer_id is per-order; the real customer is customer_unique_id.
Home state = state recorded on the customer's EARLIEST order (by purchase timestamp).
"""
from __future__ import annotations

import pandas as pd


def build_dim_customer(orders: pd.DataFrame, customers: pd.DataFrame, states: pd.DataFrame) -> pd.DataFrame:
    """orders: order_id, customer_id, order_purchase_timestamp.
    customers: customer_id, customer_unique_id, customer_state, customer_zip_code_prefix.
    """
    merged = orders[["order_id", "customer_id", "order_purchase_timestamp"]].merge(
        customers[["customer_id", "customer_unique_id", "customer_state"]], on="customer_id", how="left"
    )
    merged = merged.sort_values("order_purchase_timestamp")
    earliest = merged.drop_duplicates(subset="customer_unique_id", keep="first")

    dim = earliest[["customer_unique_id", "customer_state", "order_purchase_timestamp"]].rename(
        columns={"customer_state": "home_state_code", "order_purchase_timestamp": "first_order_date"}
    )
    dim["first_order_date"] = pd.to_datetime(dim["first_order_date"]).dt.date
    dim = dim.merge(states, how="left", left_on="home_state_code", right_on="state_code")
    dim = dim.rename(columns={"state_name": "home_state_name", "region": "home_region"})
    return dim[["customer_unique_id", "home_state_code", "home_state_name", "home_region", "first_order_date"]]
