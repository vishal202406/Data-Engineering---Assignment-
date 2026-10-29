"""Entry point: python -m pipeline.run --start-date 2017-01-01 --end-date 2018-08-31

Orchestrates extract -> transform (R1-R18) -> load. See ASSUMPTIONS.md for every
non-obvious decision, referenced by rule ID.
"""
from __future__ import annotations

import argparse
import logging
import sys

import numpy as np
import pandas as pd
from sqlalchemy import text

from pipeline.common.db import get_engine
from pipeline.common.logging_setup import setup_logging
from pipeline.common.money import round_money, round_rate
from pipeline.config import get_settings
from pipeline.extract.apis import ApiUnavailableError, fetch_fx_rates, fetch_holidays, fetch_states
from pipeline.extract.csv_sources import read_internal_files, read_kaggle_csvs
from pipeline.extract.raw_store import persist_raw
from pipeline.load import loader
from pipeline.transform.campaigns import attribute_campaigns, normalize_campaigns
from pipeline.transform.categories import resolve_product_categories
from pipeline.transform.commission import (
    assign_seller_tier,
    clean_category_overrides,
    clean_commission_rates,
    compute_seller_monthly_revenue,
    resolve_commission_rates,
)
from pipeline.transform.customers import build_dim_customer
from pipeline.transform.delivery import compute_delivery_metrics, compute_seller_lateness
from pipeline.transform.dq import DQCollector
from pipeline.transform.fx import attach_fx_rate
from pipeline.transform.geo import build_dim_seller, compute_item_distances, compute_zip_centroids
from pipeline.transform.orders import classify_orders, reconcile_payments
from pipeline.transform.reviews import dedupe_reviews

logger = logging.getLogger(__name__)

DATE_COLS = [
    "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
    "order_delivered_customer_date", "order_estimated_delivery_date",
]


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Marketplace Seller Performance Pipeline")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD, inclusive")
    return parser.parse_args(argv)


def build_dim_date(start_year: int, end_year: int, holidays: pd.DataFrame) -> pd.DataFrame:
    dates = pd.date_range(f"{start_year}-01-01", f"{end_year}-12-31", freq="D")
    national_holidays = set(pd.to_datetime(holidays.loc[holidays["state_code"] == "", "holiday_date"]).dt.date)
    df = pd.DataFrame({"date_key": dates.date})
    df["year_num"] = dates.year
    df["month_num"] = dates.month
    df["day_num"] = dates.day
    df["day_of_week"] = dates.dayofweek
    df["is_weekend"] = (dates.dayofweek >= 5).astype(int)
    df["is_national_holiday"] = df["date_key"].isin(national_holidays).astype(int)
    return df


def run(start_date: str, end_date: str) -> None:
    settings = get_settings()
    dq = DQCollector()

    # ----------------------------- EXTRACT -----------------------------
    logger.info("=== EXTRACT ===")
    frames = read_kaggle_csvs(settings.input_path)
    internal = read_internal_files(settings.internal_path)

    for col in DATE_COLS:
        frames["orders"][col] = pd.to_datetime(frames["orders"][col], errors="coerce")
    frames["order_items"]["shipping_limit_date"] = pd.to_datetime(
        frames["order_items"]["shipping_limit_date"], errors="coerce"
    )
    frames["order_reviews"]["review_answer_timestamp"] = pd.to_datetime(
        frames["order_reviews"]["review_answer_timestamp"], errors="coerce"
    )

    all_relevant_dates = pd.concat([frames["orders"][c] for c in DATE_COLS]).dropna()
    fx_start = (all_relevant_dates.min() - pd.DateOffset(days=10)).strftime("%Y-%m-%d")
    fx_end = all_relevant_dates.max().strftime("%Y-%m-%d")
    years = sorted(set(all_relevant_dates.dt.year))

    try:
        fx_rates = fetch_fx_rates(fx_start, fx_end, settings.cache_path, settings.api_timeout_seconds, settings.api_max_attempts)
        holidays = fetch_holidays(years, settings.cache_path, settings.api_timeout_seconds, settings.api_max_attempts)
        states = fetch_states(settings.cache_path, settings.api_timeout_seconds, settings.api_max_attempts, dq_log=dq.issues)
    except ApiUnavailableError as exc:
        logger.error("A required API is unavailable; aborting run (no partial load). %s", exc)
        sys.exit(1)

    persist_raw(settings.raw_path, frames, internal, {"fx_rates": fx_rates, "holidays": holidays, "states": states})

    # ----------------------------- TRANSFORM: reference data -----------------------------
    logger.info("=== TRANSFORM: reference data ===")
    commission_rates = clean_commission_rates(internal["commission_rates"], dq)
    category_overrides = clean_category_overrides(internal["category_overrides"], dq)

    dim_product = resolve_product_categories(frames["products"], frames["category_translation"], internal["category_patch"], dq)

    zip_centroids = compute_zip_centroids(frames["geolocation"])
    dim_seller = build_dim_seller(frames["sellers"], zip_centroids, states)
    dim_customer = build_dim_customer(frames["orders"], frames["customers"], states)

    campaigns = normalize_campaigns(internal["campaigns"])

    # ----------------------------- TRANSFORM: R12 seed (full history) -----------------------------
    # Seller tier depends on the PREVIOUS calendar month's revenue; computed from full order
    # history (not range-filtered) so sub-range re-runs and the first month of any range still
    # see correct prior-month revenue. See ASSUMPTIONS.md R12.
    seller_revenue_full = compute_seller_monthly_revenue(frames["order_items"], frames["orders"])

    # ----------------------------- TRANSFORM: scope to requested range -----------------------------
    logger.info("=== TRANSFORM: scoping to [%s, %s] ===", start_date, end_date)
    start_ts, end_ts = pd.Timestamp(start_date), pd.Timestamp(end_date) + pd.DateOffset(days=1) - pd.Timedelta(microseconds=1)
    in_range = (frames["orders"]["order_purchase_timestamp"] >= start_ts) & (frames["orders"]["order_purchase_timestamp"] <= end_ts)
    range_orders_raw = frames["orders"][in_range].copy()
    total_loaded_raw = len(range_orders_raw)

    range_orders = classify_orders(range_orders_raw, dq)  # R2, R3
    order_id_set = set(range_orders["order_id"])

    range_items = frames["order_items"][frames["order_items"]["order_id"].isin(order_id_set)].copy()
    range_payments = frames["order_payments"][frames["order_payments"]["order_id"].isin(order_id_set)].copy()
    range_reviews_raw = frames["order_reviews"][frames["order_reviews"]["order_id"].isin(order_id_set)].copy()

    # R4: item gross
    range_items["item_gross_brl"] = range_items["price"] + range_items["freight_value"]

    # R5: payment reconciliation (order-level)
    payment_recon = reconcile_payments(range_items, range_payments, dq)
    range_orders = range_orders.merge(payment_recon, on="order_id", how="left")
    for col in ("net_cash_paid_brl", "payment_total_brl"):
        range_orders[col] = range_orders[col].fillna(0.0)
    range_orders["payment_mismatch"] = range_orders["payment_mismatch"].fillna(False)

    # R6: FX conversion at order level, then broadcast to items
    range_orders["fx_lookup_date"] = range_orders["order_approved_at"].fillna(range_orders["order_purchase_timestamp"]).dt.date
    range_orders = attach_fx_rate(range_orders, fx_rates, "fx_lookup_date", dq, "order", ["order_id"])
    range_items = range_items.merge(range_orders[["order_id", "fx_rate", "fx_rate_date"]], on="order_id", how="left")
    range_items["item_gross_usd"] = range_items["item_gross_brl"] * range_items["fx_rate"].astype(float)

    order_gross = range_items.groupby("order_id")[["item_gross_brl", "item_gross_usd"]].sum()
    order_gross.columns = ["gross_brl", "gross_usd"]
    range_orders = range_orders.merge(order_gross, on="order_id", how="left")
    range_orders["gross_brl"] = range_orders["gross_brl"].fillna(0.0)
    range_orders["gross_usd"] = range_orders["gross_usd"].fillna(0.0)

    # R1/R17: per-order customer state & region (order/shipment-level; distinct from dim_customer's
    # aggregated home_state). See ASSUMPTIONS.md R9/R18.
    cust_lookup = frames["customers"][["customer_id", "customer_unique_id", "customer_state", "customer_zip_code_prefix"]]
    range_orders = range_orders.merge(cust_lookup, on="customer_id", how="left")
    range_orders = range_orders.merge(states, how="left", left_on="customer_state", right_on="state_code")
    range_orders = range_orders.rename(columns={"state_name": "customer_state_name", "region": "customer_region"})

    # R9/R10: delivery metrics
    range_orders = compute_delivery_metrics(range_orders, holidays, dq)

    # R11: per-seller lateness within each order
    seller_lateness = compute_seller_lateness(range_items, range_orders)

    # R14: reviews (dedupe, keep latest review_answer_timestamp)
    latest_reviews = dedupe_reviews(range_reviews_raw, dq)
    range_orders = range_orders.merge(latest_reviews, on="order_id", how="left")
    seller_lateness = seller_lateness.merge(latest_reviews, on="order_id", how="left")

    # R8: category onto items
    range_items = range_items.merge(dim_product[["product_id", "category_name_en"]], on="product_id", how="left")

    # R12: seller tier at purchase month
    range_items = range_items.merge(
        range_orders[["order_id", "order_purchase_timestamp"]], on="order_id", how="left"
    )
    range_items["purchase_year_month"] = range_items["order_purchase_timestamp"].dt.to_period("M")
    range_items = assign_seller_tier(range_items, seller_revenue_full)
    range_items["purchase_month"] = range_items["purchase_year_month"].astype(str)

    # R13: commission rate, commission_brl/usd
    range_items["purchase_date"] = range_items["order_purchase_timestamp"]
    range_items = resolve_commission_rates(range_items, commission_rates, category_overrides, dq)
    range_items["commission_rate"] = pd.to_numeric(range_items["commission_rate"], errors="coerce")
    range_items["commission_brl"] = range_items["price"] * range_items["commission_rate"]
    range_items["commission_usd"] = range_items["commission_brl"] * range_items["fx_rate"].astype(float)

    # R15/R16: distance
    range_items = range_items.merge(
        dim_seller[["seller_id", "latitude", "longitude"]].rename(columns={"latitude": "seller_lat", "longitude": "seller_lng"}),
        on="seller_id", how="left",
    )
    range_items = range_items.merge(
        range_orders[["order_id", "customer_zip_code_prefix"]], on="order_id", how="left"
    )
    range_items = range_items.merge(
        zip_centroids.rename(columns={"zip_prefix": "customer_zip_code_prefix", "latitude": "customer_lat", "longitude": "customer_lng"}),
        on="customer_zip_code_prefix", how="left",
    )
    range_items["distance_km"] = compute_item_distances(range_items, dq)

    # R18: campaign attribution (needs order-level customer_state/region + item category)
    range_items = range_items.merge(
        range_orders[["order_id", "customer_state", "customer_region"]], on="order_id", how="left"
    )
    range_items["purchase_ts_sp"] = range_items["order_purchase_timestamp"]
    range_items["campaign_id"] = attribute_campaigns(range_items, campaigns)

    # ----------------------------- shape final frames for load -----------------------------
    fact_order = range_orders[[
        "order_id", "customer_unique_id", "order_status", "customer_state", "customer_region",
        "is_revenue_eligible", "has_broken_delivery",
        "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
        "order_delivered_customer_date", "order_estimated_delivery_date", "gross_brl", "gross_usd",
        "net_cash_paid_brl", "payment_total_brl", "payment_mismatch", "fx_rate", "fx_rate_date",
        "on_time", "delivery_business_days", "review_score",
    ]].rename(columns={
        "order_purchase_timestamp": "purchase_ts", "order_approved_at": "approved_at",
        "order_delivered_carrier_date": "delivered_carrier_date",
        "order_delivered_customer_date": "delivered_customer_date",
        "order_estimated_delivery_date": "estimated_delivery_date",
        "customer_state": "customer_state_code",
    }).copy()
    fact_order["gross_brl"] = round_money(fact_order["gross_brl"])
    fact_order["gross_usd"] = round_money(fact_order["gross_usd"])
    fact_order["net_cash_paid_brl"] = round_money(fact_order["net_cash_paid_brl"])
    fact_order["payment_total_brl"] = round_money(fact_order["payment_total_brl"])
    fact_order["fx_rate"] = round_rate(fact_order["fx_rate"])
    fact_order["is_revenue_eligible"] = fact_order["is_revenue_eligible"].astype(int)
    fact_order["has_broken_delivery"] = fact_order["has_broken_delivery"].astype(int)
    fact_order["payment_mismatch"] = fact_order["payment_mismatch"].astype(int)
    fact_order["estimated_delivery_date"] = pd.to_datetime(fact_order["estimated_delivery_date"]).dt.date

    fact_order_item = range_items[[
        "order_id", "order_item_id", "seller_id", "product_id", "price", "freight_value",
        "item_gross_brl", "item_gross_usd", "fx_rate", "fx_rate_date", "category_name_en",
        "seller_tier_at_purchase", "commission_rate", "commission_brl", "commission_usd",
        "shipping_limit_date", "distance_km", "campaign_id", "purchase_month",
    ]].rename(columns={"price": "price_brl", "freight_value": "freight_value_brl"}).copy()
    for col in ("price_brl", "freight_value_brl", "item_gross_brl", "item_gross_usd", "commission_brl", "commission_usd"):
        fact_order_item[col] = round_money(fact_order_item[col])
    fact_order_item["fx_rate"] = round_rate(fact_order_item["fx_rate"])
    fact_order_item["commission_rate"] = fact_order_item["commission_rate"].round(6)
    fact_order_item["distance_km"] = fact_order_item["distance_km"].round(3)

    fact_order_seller = seller_lateness.merge(
        range_items[["order_id", "seller_id", "purchase_month"]].drop_duplicates(), on=["order_id", "seller_id"], how="left"
    )

    run_counts = {
        "orders_raw_in_range": total_loaded_raw,
        "orders_loaded": len(fact_order),
        "order_items_loaded": len(fact_order_item),
    }

    # ----------------------------- LOAD -----------------------------
    logger.info("=== LOAD ===")
    engine = get_engine()
    with engine.begin() as conn:
        run_id = loader.start_run(conn, start_date, end_date, {"start_date": start_date, "end_date": end_date})

        fact_order["run_id"] = run_id
        fact_order_item["run_id"] = run_id
        fact_order_seller["run_id"] = run_id

        loader.upsert_ref_state(conn, states)
        loader.upsert_ref_fx_rate(conn, fx_rates)
        loader.upsert_ref_holiday(conn, holidays)
        loader.reload_ref_commission_rate(conn, commission_rates)
        loader.reload_ref_category_override(conn, category_overrides)

        loader.upsert_dim_customer(conn, dim_customer)
        loader.upsert_dim_seller(conn, dim_seller)
        loader.upsert_dim_product(conn, dim_product)
        loader.upsert_dim_campaign(conn, campaigns)
        dim_date = build_dim_date(min(years), max(years), holidays)
        loader.upsert_dim_date(conn, dim_date)

        fact_counts = loader.replace_facts_for_range(conn, start_date, end_date, fact_order, fact_order_item, fact_order_seller)
        run_counts.update(fact_counts)

        n_issues = loader.insert_dq_issues(conn, dq.issues, run_id)
        run_counts["data_quality_issue"] = n_issues

        loader.finish_run(conn, run_id, "success", run_counts)

    logger.info("Run %s complete: %s", run_id, run_counts)


def main(argv=None) -> None:
    setup_logging()
    args = parse_args(argv)
    run(args.start_date, args.end_date)


if __name__ == "__main__":
    main()
