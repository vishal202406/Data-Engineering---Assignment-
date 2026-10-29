"""R6 — currency conversion: exact-date match, weekend/holiday fallback to most recent
EARLIER rate (never later), and a clear DQ log entry when no earlier rate exists at all."""
import pandas as pd

from pipeline.transform.dq import DQCollector
from pipeline.transform.fx import attach_fx_rate


def _fx_rates():
    return pd.DataFrame({
        "rate_date": [pd.Timestamp("2018-01-02").date(), pd.Timestamp("2018-01-03").date(), pd.Timestamp("2018-01-05").date()],
        "brl_to_usd": [0.30, 0.31, 0.32],
    })


def test_exact_date_match_used():
    df = pd.DataFrame({"id": [1], "lookup_date": [pd.Timestamp("2018-01-03").date()]})
    dq = DQCollector()
    out = attach_fx_rate(df, _fx_rates(), "lookup_date", dq, "order", ["id"])
    assert float(out.loc[0, "fx_rate"]) == 0.31
    assert out.loc[0, "fx_rate_date"] == pd.Timestamp("2018-01-03")


def test_weekend_falls_back_to_most_recent_earlier_rate():
    # 2018-01-04 has no rate in our fixture; must use 2018-01-03 (0.31), never 2018-01-05 (0.32).
    df = pd.DataFrame({"id": [1], "lookup_date": [pd.Timestamp("2018-01-04").date()]})
    dq = DQCollector()
    out = attach_fx_rate(df, _fx_rates(), "lookup_date", dq, "order", ["id"])
    assert float(out.loc[0, "fx_rate"]) == 0.31
    assert out.loc[0, "fx_rate_date"] == pd.Timestamp("2018-01-03")


def test_no_earlier_rate_logs_dq_and_leaves_null():
    df = pd.DataFrame({"id": ["ord1"], "lookup_date": [pd.Timestamp("2017-12-01").date()]})
    dq = DQCollector()
    out = attach_fx_rate(df, _fx_rates(), "lookup_date", dq, "order", ["id"])
    assert pd.isna(out.loc[0, "fx_rate"])
    assert any(i["rule_id"] == "R6" and i["entity_id"] == "ord1" for i in dq.issues)
