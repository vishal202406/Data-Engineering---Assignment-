"""R8 — category resolution: patch wins over Kaggle translation; else 'unknown' (has a PT
name but no translation) or 'uncategorized' (no PT category at all). The two stay distinguishable.
"""
from __future__ import annotations

import pandas as pd

from pipeline.transform.dq import DQCollector

UNKNOWN = "unknown"
UNCATEGORIZED = "uncategorized"


def resolve_product_categories(
    products: pd.DataFrame,
    category_translation: pd.DataFrame,
    category_patch: pd.DataFrame,
    dq: DQCollector,
) -> pd.DataFrame:
    patch_map = dict(
        zip(category_patch["product_category_name"], category_patch["product_category_name_english"])
    )
    kaggle_map = dict(
        zip(category_translation["product_category_name"], category_translation["product_category_name_english"])
    )

    def resolve(pt_name) -> tuple[str | None, str]:
        if pd.isna(pt_name) or str(pt_name).strip() == "":
            return None, UNCATEGORIZED
        if pt_name in patch_map:
            return patch_map[pt_name], "patch"
        if pt_name in kaggle_map:
            return kaggle_map[pt_name], "kaggle"
        return None, UNKNOWN

    out = products[["product_id", "product_category_name"]].copy()
    resolved = out["product_category_name"].apply(resolve)
    out["category_name_en"] = resolved.apply(lambda t: t[0] if t[0] is not None else t[1])
    out["category_source"] = resolved.apply(lambda t: t[1])

    for _, row in out[out["category_source"] == UNKNOWN].iterrows():
        dq.log(
            "R8", "product", row["product_id"], "category_unknown",
            f"Portuguese category '{row['product_category_name']}' has no translation.",
        )
    for _, row in out[out["category_source"] == UNCATEGORIZED].iterrows():
        dq.log("R8", "product", row["product_id"], "category_uncategorized", "Product has no category at all.")

    return out.rename(columns={"product_category_name": "category_name_pt"})
