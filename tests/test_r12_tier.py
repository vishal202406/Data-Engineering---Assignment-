"""R12 — seller tier for month M is based on revenue-eligible (status delivered) price total
in month M-1. No sales last month => Bronze. Boundaries: <5000 Bronze, [5000,20000] Silver, >20000 Gold."""
import pandas as pd

from pipeline.transform.commission import assign_seller_tier, bucket_tier, compute_seller_monthly_revenue


def test_bucket_boundaries():
    assert bucket_tier(0) == "Bronze"
    assert bucket_tier(4999.99) == "Bronze"
    assert bucket_tier(5000) == "Silver"
    assert bucket_tier(20000) == "Silver"
    assert bucket_tier(20000.01) == "Gold"


def test_revenue_only_counts_delivered_orders():
    orders = pd.DataFrame([
        {"order_id": "o1", "order_status": "delivered", "order_purchase_timestamp": pd.Timestamp("2018-02-10")},
        {"order_id": "o2", "order_status": "canceled", "order_purchase_timestamp": pd.Timestamp("2018-02-15")},
    ])
    items = pd.DataFrame([
        {"order_id": "o1", "seller_id": "s1", "price": 6000.0},
        {"order_id": "o2", "seller_id": "s1", "price": 50000.0},  # canceled: must not count
    ])
    revenue = compute_seller_monthly_revenue(items, orders)
    row = revenue[(revenue["seller_id"] == "s1") & (revenue["year_month"] == pd.Period("2018-02", freq="M"))]
    assert row["revenue_brl"].iloc[0] == 6000.0


def test_tier_uses_previous_month_and_defaults_to_bronze_with_no_history():
    revenue = pd.DataFrame({
        "seller_id": ["s1"],
        "year_month": [pd.Period("2018-02", freq="M")],
        "revenue_brl": [25000.0],  # Gold-level revenue in Feb
    })
    items = pd.DataFrame({
        "seller_id": ["s1", "s1", "s2"],
        "purchase_year_month": [pd.Period("2018-03", freq="M"), pd.Period("2018-02", freq="M"), pd.Period("2018-03", freq="M")],
    })
    out = assign_seller_tier(items, revenue)
    # s1 in March is priced off February's 25000 (Gold); s1 in February is priced off January (no data -> Bronze).
    assert out.loc[0, "seller_tier_at_purchase"] == "Gold"
    assert out.loc[1, "seller_tier_at_purchase"] == "Bronze"
    # s2 has no revenue history at all -> Bronze.
    assert out.loc[2, "seller_tier_at_purchase"] == "Bronze"
