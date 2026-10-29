"""Reads the Kaggle Olist CSVs and the human-maintained internal files.

Everything here returns data exactly as found on disk (R: raw/staging layer must be
as-received). Any cleaning happens later in pipeline.transform.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

KAGGLE_FILES = {
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "order_reviews": "olist_order_reviews_dataset.csv",
    "customers": "olist_customers_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
    "products": "olist_products_dataset.csv",
    "geolocation": "olist_geolocation_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

# Zip-code-prefix columns must stay strings (leading zeros matter).
_STR_COLS = {
    "orders": [],
    "order_items": [],
    "order_payments": [],
    "order_reviews": [],
    "customers": ["customer_zip_code_prefix"],
    "sellers": ["seller_zip_code_prefix"],
    "products": [],
    "geolocation": ["geolocation_zip_code_prefix"],
    "category_translation": [],
}


def read_kaggle_csvs(input_dir: Path) -> dict[str, pd.DataFrame]:
    """Reads all required Kaggle CSVs from a configurable folder."""
    frames: dict[str, pd.DataFrame] = {}
    for key, filename in KAGGLE_FILES.items():
        path = input_dir / filename
        if not path.exists():
            raise FileNotFoundError(
                f"Required Kaggle file missing: {path}. "
                f"Download the Olist dataset and place it in {input_dir}."
            )
        dtype = {c: str for c in _STR_COLS[key]}
        frames[key] = pd.read_csv(path, dtype=dtype)
        logger.info("Loaded %s rows=%d cols=%d", filename, len(frames[key]), frames[key].shape[1])
    return frames


def read_internal_files(internal_dir: Path) -> dict[str, object]:
    """Reads the human-maintained internal reference files, as-is."""
    commission_rates = pd.read_csv(internal_dir / "commission_rates.csv", dtype=str)
    category_overrides = pd.read_csv(internal_dir / "category_commission_overrides.csv", dtype=str)
    category_patch = pd.read_csv(internal_dir / "category_translation_patch.csv", dtype=str)
    with open(internal_dir / "campaigns.json", "r", encoding="utf-8") as f:
        campaigns = json.load(f)

    logger.info(
        "Loaded internal files: commission_rates=%d category_overrides=%d "
        "category_patch=%d campaigns=%d",
        len(commission_rates), len(category_overrides), len(category_patch), len(campaigns),
    )
    return {
        "commission_rates": commission_rates,
        "category_overrides": category_overrides,
        "category_patch": category_patch,
        "campaigns": campaigns,
    }
