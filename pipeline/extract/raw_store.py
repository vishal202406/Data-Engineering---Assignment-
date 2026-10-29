"""Persists extracted data as-received into the raw/staging layer (data/raw/), before any
business rules are applied. Uses Parquet so dtypes round-trip exactly.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def persist_raw(raw_dir: Path, frames: dict[str, pd.DataFrame], internal: dict, api_frames: dict[str, pd.DataFrame]) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)

    for name, df in frames.items():
        df.to_parquet(raw_dir / f"stg_{name}.parquet", index=False)

    internal["commission_rates"].to_parquet(raw_dir / "stg_commission_rates.parquet", index=False)
    internal["category_overrides"].to_parquet(raw_dir / "stg_category_overrides.parquet", index=False)
    internal["category_patch"].to_parquet(raw_dir / "stg_category_patch.parquet", index=False)
    with open(raw_dir / "stg_campaigns.json", "w", encoding="utf-8") as f:
        json.dump(internal["campaigns"], f)

    for name, df in api_frames.items():
        df.to_parquet(raw_dir / f"stg_api_{name}.parquet", index=False)

    logger.info("Raw layer persisted to %s", raw_dir)
