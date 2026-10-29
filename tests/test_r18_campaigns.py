"""R18 — campaign attribution: window matching (date-only end is full-day inclusive; 'Z'
timestamps convert UTC->America/Sao_Paulo), geo/category ALL semantics, and priority with
later-start tiebreak."""
import pandas as pd

from pipeline.transform.campaigns import attribute_campaigns, normalize_campaigns

RAW_CAMPAIGNS = [
    {"campaign_id": "C001", "name": "Black Friday 2017", "start": "2017-11-24", "end": "2017-11-27",
     "target_states": ["ALL"], "categories": ["ALL"], "priority": 1},
    {"campaign_id": "C003", "name": "Tech Week", "start": "2018-03-05T03:00:00Z", "end": "2018-03-12T02:59:59Z",
     "target_states": ["SP", "RJ", "MG"], "categories": ["computers_accessories", "electronics", "telephony"], "priority": 2},
    {"campaign_id": "C004", "name": "Mother's Day", "start": "2018-05-01", "end": "2018-05-13",
     "target_states": ["ALL"], "categories": ["health_beauty", "perfumery", "watches_gifts"], "priority": 2},
    {"campaign_id": "C005", "name": "Mother's Day South", "start": "2018-05-07", "end": "2018-05-13",
     "target_regions": ["Sul"], "categories": ["health_beauty", "perfumery"], "priority": 2},
]


def test_utc_z_timestamp_converts_to_sao_paulo_local():
    campaigns = normalize_campaigns(RAW_CAMPAIGNS)
    c003 = campaigns[campaigns["campaign_id"] == "C003"].iloc[0]
    # 2018-03-05T03:00:00Z is UTC-3 in SP (DST that week) -> 2018-03-05 00:00:00 local.
    assert c003["start_ts_sp"] == pd.Timestamp("2018-03-05 00:00:00")


def test_date_only_end_includes_full_day():
    campaigns = normalize_campaigns(RAW_CAMPAIGNS)
    c001 = campaigns[campaigns["campaign_id"] == "C001"].iloc[0]
    assert c001["end_ts_sp"] == pd.Timestamp("2017-11-27 23:59:59.999999")


def test_priority_breaks_tie_by_later_start():
    # An item on 2018-05-10, region Sul, category health_beauty matches both C004 (priority 2,
    # starts 05-01) and C005 (priority 2, starts 05-07, region-only). Tie on priority -> later start (C005) wins.
    campaigns = normalize_campaigns(RAW_CAMPAIGNS)
    items = pd.DataFrame({
        "purchase_ts_sp": [pd.Timestamp("2018-05-10")],
        "customer_state": ["RS"],
        "customer_region": ["Sul"],
        "category_name_en": ["health_beauty"],
    })
    result = attribute_campaigns(items, campaigns)
    assert result.iloc[0] == "C005"


def test_no_match_gives_none():
    campaigns = normalize_campaigns(RAW_CAMPAIGNS)
    items = pd.DataFrame({
        "purchase_ts_sp": [pd.Timestamp("2018-01-01")],
        "customer_state": ["SP"],
        "customer_region": ["Sudeste"],
        "category_name_en": ["toys"],
    })
    result = attribute_campaigns(items, campaigns)
    assert result.iloc[0] is None


def test_all_means_any_for_state_and_category():
    campaigns = normalize_campaigns(RAW_CAMPAIGNS)
    items = pd.DataFrame({
        "purchase_ts_sp": [pd.Timestamp("2017-11-25")],
        "customer_state": ["AM"],  # not in any explicit list, but C001 targets ALL
        "customer_region": ["Norte"],
        "category_name_en": ["anything_goes"],
    })
    result = attribute_campaigns(items, campaigns)
    assert result.iloc[0] == "C001"
