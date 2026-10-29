"""R9 — business-day lead time. Convention under test: start day INCLUDED if a business day,
delivery day EXCLUDED (numpy.busday_count semantics). National holidays always count; a
regional holiday only counts for an order whose own customer_state matches it."""
import pandas as pd

from pipeline.transform.delivery import compute_delivery_metrics
from pipeline.transform.dq import DQCollector


def _holidays():
    return pd.DataFrame([
        {"holiday_date": pd.Timestamp("2018-03-08").date(), "state_code": "", "name": "National Holiday"},
        {"holiday_date": pd.Timestamp("2018-03-09").date(), "state_code": "SP", "name": "SP Regional Holiday"},
    ])


def _base_order(**overrides):
    row = {
        "order_id": "o1",
        "customer_state": "SP",
        "has_broken_delivery": False,
        "order_approved_at": pd.Timestamp("2018-03-05 10:00:00"),  # Monday
        "order_purchase_timestamp": pd.Timestamp("2018-03-05 09:00:00"),
        "order_delivered_customer_date": pd.Timestamp("2018-03-12 10:00:00"),  # Monday next week
        "order_estimated_delivery_date": pd.Timestamp("2018-03-15"),
    }
    row.update(overrides)
    return row


def test_same_day_approval_and_delivery_is_zero_business_days():
    orders = pd.DataFrame([_base_order(
        order_delivered_customer_date=pd.Timestamp("2018-03-05 18:00:00"),
    )])
    out = compute_delivery_metrics(orders, _holidays(), DQCollector())
    assert out.loc[0, "delivery_business_days"] == 0


def test_national_holiday_excluded_regardless_of_state():
    # Mon 2018-03-05 -> Mon 2018-03-12, minus weekend (10,11) and national holiday (8).
    # Business days in [5..11]: 5,6,7,9 (8 is holiday) = 4.
    orders = pd.DataFrame([_base_order(customer_state="RJ")])  # not SP -> SP-regional holiday should NOT count
    out = compute_delivery_metrics(orders, _holidays(), DQCollector())
    assert out.loc[0, "delivery_business_days"] == 4


def test_regional_holiday_only_counts_for_matching_state():
    # Same window but customer is in SP: both national (8) and SP-regional (9) holidays excluded.
    orders = pd.DataFrame([_base_order(customer_state="SP")])
    out = compute_delivery_metrics(orders, _holidays(), DQCollector())
    assert out.loc[0, "delivery_business_days"] == 3


def test_broken_delivery_yields_null_metrics():
    orders = pd.DataFrame([_base_order(has_broken_delivery=True, order_delivered_customer_date=pd.NaT)])
    out = compute_delivery_metrics(orders, _holidays(), DQCollector())
    assert out.loc[0, "delivery_business_days"] is None
    assert out.loc[0, "on_time"] is None
