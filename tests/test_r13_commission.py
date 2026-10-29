"""R13 — commission rate resolution (category override wins over tier rate), plus cleaning
of the messy internal CSVs: case/whitespace normalization and rejection of impossible
(effective_to < effective_from) ranges, every rejection logged."""
import pandas as pd

from pipeline.transform.commission import clean_category_overrides, clean_commission_rates, resolve_commission_rates
from pipeline.transform.dq import DQCollector


def test_clean_commission_rates_normalizes_case_and_logs_it():
    raw = pd.DataFrame([
        {"tier": "gold", "rate": "0.09", "effective_from": "2018-01-01", "effective_to": ""},
        {"tier": "Bronze", "rate": "0.16", "effective_from": "2018-01-01", "effective_to": ""},
    ])
    dq = DQCollector()
    cleaned = clean_commission_rates(raw, dq)
    assert set(cleaned["tier"]) == {"Gold", "Bronze"}
    assert any(i["issue_type"] == "corrected_tier_case" for i in dq.issues)


def test_clean_category_overrides_rejects_impossible_range():
    raw = pd.DataFrame([
        {"category_name_en": "watches_gifts", "fixed_rate": "0.20", "effective_from": "2018-05-01",
         "effective_to": "2018-04-30", "notes": "entered by sales team"},
        {"category_name_en": " Telephony ", "fixed_rate": "0.07", "effective_from": "2018-01-01",
         "effective_to": "", "notes": "renegotiated"},
    ])
    dq = DQCollector()
    cleaned = clean_category_overrides(raw, dq)
    assert "watches_gifts" not in cleaned["category_name_en"].values
    assert "telephony" in cleaned["category_name_en"].values  # trimmed + lowercased
    assert any(i["issue_type"] == "rejected_impossible_range" for i in dq.issues)
    assert any(i["issue_type"] == "corrected_category_whitespace_case" for i in dq.issues)


def test_category_override_takes_precedence_over_tier_rate():
    commission_rates = pd.DataFrame({
        "tier": ["Gold"], "rate": [0.10],
        "effective_from": [pd.Timestamp("2016-01-01")], "effective_to": [None],
    })
    category_overrides = pd.DataFrame({
        "category_name_en": ["computers"], "fixed_rate": [0.05],
        "effective_from": [pd.Timestamp("2017-06-01")], "effective_to": [None], "notes": [None],
    })
    items = pd.DataFrame({
        "order_id": ["o1"], "order_item_id": [1], "category_name_en": ["computers"],
        "seller_tier_at_purchase": ["Gold"], "purchase_date": [pd.Timestamp("2018-01-01")],
    })
    dq = DQCollector()
    out = resolve_commission_rates(items, commission_rates, category_overrides, dq)
    assert float(out.loc[0, "commission_rate"]) == 0.05  # override, not the 0.10 tier rate


def test_purchase_late_on_last_covered_day_still_matches():
    # effective_to is a calendar DATE and covers the whole day; a purchase late in the evening
    # on that last day must still match (regression: previously compared full timestamps).
    commission_rates = pd.DataFrame({
        "tier": ["Bronze"], "rate": [0.15],
        "effective_from": [pd.Timestamp("2016-01-01")], "effective_to": [pd.Timestamp("2017-12-31")],
    })
    items = pd.DataFrame({
        "order_id": ["o1"], "order_item_id": [1], "category_name_en": ["toys"],
        "seller_tier_at_purchase": ["Bronze"], "purchase_date": [pd.Timestamp("2017-12-31 23:29:31")],
    })
    dq = DQCollector()
    out = resolve_commission_rates(items, commission_rates, pd.DataFrame(), dq)
    assert float(out.loc[0, "commission_rate"]) == 0.15
    assert not dq.issues


def test_no_rate_found_is_logged_not_defaulted():
    items = pd.DataFrame({
        "order_id": ["o1"], "order_item_id": [1], "category_name_en": ["toys"],
        "seller_tier_at_purchase": ["Gold"], "purchase_date": [pd.Timestamp("2015-01-01")],  # before any rate starts
    })
    commission_rates = pd.DataFrame({
        "tier": ["Gold"], "rate": [0.10],
        "effective_from": [pd.Timestamp("2016-01-01")], "effective_to": [None],
    })
    dq = DQCollector()
    out = resolve_commission_rates(items, commission_rates, pd.DataFrame(), dq)
    assert pd.isna(out.loc[0, "commission_rate"])
    assert any(i["issue_type"] == "no_commission_rate_found" for i in dq.issues)
