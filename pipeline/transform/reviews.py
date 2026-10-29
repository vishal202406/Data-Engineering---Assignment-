"""R14 — reviews file has duplicates; keep the one with the latest review_answer_timestamp
per order. The resulting score is attributed to every seller in that order (done by the caller
via a join against order items, since that's where the seller list lives)."""
from __future__ import annotations

import pandas as pd

from pipeline.transform.dq import DQCollector


def dedupe_reviews(reviews: pd.DataFrame, dq: DQCollector) -> pd.DataFrame:
    reviews = reviews.copy()
    reviews["review_answer_timestamp"] = pd.to_datetime(reviews["review_answer_timestamp"])
    dupe_counts = reviews.groupby("order_id").size()
    dupes = dupe_counts[dupe_counts > 1]
    for order_id, count in dupes.items():
        dq.log("R14", "order", order_id, "duplicate_reviews",
               f"{count} reviews found for this order; kept the one with the latest review_answer_timestamp.")

    reviews = reviews.sort_values("review_answer_timestamp")
    latest = reviews.drop_duplicates(subset="order_id", keep="last")
    return latest[["order_id", "review_score"]]
