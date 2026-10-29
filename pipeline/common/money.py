"""Decimal rounding helpers. R7: round only at the final stored value, 2dp for money,
6dp for rates — never in intermediate steps.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

import pandas as pd


def round_money(series: pd.Series) -> pd.Series:
    return series.apply(lambda x: _round(x, "0.01") if pd.notna(x) else x)


def round_rate(series: pd.Series) -> pd.Series:
    return series.apply(lambda x: _round(x, "0.000001") if pd.notna(x) else x)


def _round(value, quant: str) -> Decimal:
    return Decimal(str(value)).quantize(Decimal(quant), rounding=ROUND_HALF_UP)
