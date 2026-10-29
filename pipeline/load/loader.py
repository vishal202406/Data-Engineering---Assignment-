"""Loads transformed data into MySQL.

Idempotency strategy (see README "Idempotency & re-runs"):
  - Dimensions (dim_*) and growing reference tables (ref_fx_rate, ref_holiday, ref_state):
    upserted by natural key via INSERT ... ON DUPLICATE KEY UPDATE. Safe to re-run anytime.
  - Tiny config reference tables derived fresh from the internal CSVs every run
    (ref_commission_rate, ref_category_override): truncated and reloaded in full each run.
  - Facts (fact_order, fact_order_item, fact_order_seller): delete-then-insert, scoped to the
    requested [start_date, end_date] purchase-date range only, so re-running a sub-range (e.g.
    a single month) replaces exactly that slice without touching other months.
  - data_quality_issue and pipeline_run are append-only logs, scoped by run_id.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import math

import pandas as pd
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

logger = logging.getLogger(__name__)


def _clean_value(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (pd.Timestamp,)):
        if pd.isna(v):
            return None
        return v.to_pydatetime()
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
        if isinstance(v, float) and math.isnan(v):
            return None
        return v
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def df_to_records(df: pd.DataFrame) -> list[dict]:
    records = df.to_dict(orient="records")
    return [{k: _clean_value(v) for k, v in rec.items()} for rec in records]


def _execute_many(conn: Connection, sql: str, records: list[dict], chunk_size: int = 2000) -> int:
    total = 0
    stmt = text(sql)
    for i in range(0, len(records), chunk_size):
        chunk = records[i : i + chunk_size]
        if not chunk:
            continue
        conn.execute(stmt, chunk)
        total += len(chunk)
    return total


# ========================= pipeline_run =========================

def start_run(conn: Connection, start_date: str, end_date: str, params: dict) -> int:
    result = conn.execute(
        text(
            "INSERT INTO pipeline_run (start_date, end_date, started_at, status, params_json) "
            "VALUES (:start_date, :end_date, :started_at, 'running', :params_json)"
        ),
        {
            "start_date": start_date,
            "end_date": end_date,
            "started_at": dt.datetime.now(),
            "params_json": json.dumps(params),
        },
    )
    return result.lastrowid


def finish_run(conn: Connection, run_id: int, status: str, row_counts: dict) -> None:
    conn.execute(
        text(
            "UPDATE pipeline_run SET finished_at = :finished_at, status = :status, "
            "row_counts_json = :row_counts WHERE run_id = :run_id"
        ),
        {
            "finished_at": dt.datetime.now(),
            "status": status,
            "row_counts": json.dumps(row_counts),
            "run_id": run_id,
        },
    )


# ========================= dimensions (upsert) =========================

def upsert_dim_customer(conn: Connection, df: pd.DataFrame) -> int:
    sql = """
        INSERT INTO dim_customer (customer_unique_id, home_state_code, home_state_name, home_region, first_order_date)
        VALUES (:customer_unique_id, :home_state_code, :home_state_name, :home_region, :first_order_date)
        ON DUPLICATE KEY UPDATE home_state_code=VALUES(home_state_code), home_state_name=VALUES(home_state_name),
            home_region=VALUES(home_region), first_order_date=VALUES(first_order_date)
    """
    return _execute_many(conn, sql, df_to_records(df))


def upsert_dim_seller(conn: Connection, df: pd.DataFrame) -> int:
    sql = """
        INSERT INTO dim_seller (seller_id, seller_zip_prefix, seller_state_code, seller_state_name, seller_region, latitude, longitude)
        VALUES (:seller_id, :seller_zip_prefix, :seller_state_code, :seller_state_name, :seller_region, :latitude, :longitude)
        ON DUPLICATE KEY UPDATE seller_zip_prefix=VALUES(seller_zip_prefix), seller_state_code=VALUES(seller_state_code),
            seller_state_name=VALUES(seller_state_name), seller_region=VALUES(seller_region),
            latitude=VALUES(latitude), longitude=VALUES(longitude)
    """
    return _execute_many(conn, sql, df_to_records(df))


def upsert_dim_product(conn: Connection, df: pd.DataFrame) -> int:
    sql = """
        INSERT INTO dim_product (product_id, category_name_pt, category_name_en, category_source)
        VALUES (:product_id, :category_name_pt, :category_name_en, :category_source)
        ON DUPLICATE KEY UPDATE category_name_pt=VALUES(category_name_pt), category_name_en=VALUES(category_name_en),
            category_source=VALUES(category_source)
    """
    return _execute_many(conn, sql, df_to_records(df))


def upsert_dim_campaign(conn: Connection, df: pd.DataFrame) -> int:
    records = df_to_records(df)
    for r in records:
        r["target_states"] = json.dumps(r["target_states"]) if r["target_states"] is not None else None
        r["target_regions"] = json.dumps(r["target_regions"]) if r["target_regions"] is not None else None
        r["categories"] = json.dumps(r["categories"]) if r["categories"] is not None else None
    sql = """
        INSERT INTO dim_campaign (campaign_id, name, start_ts_sp, end_ts_sp, target_states, target_regions, categories, priority)
        VALUES (:campaign_id, :name, :start_ts_sp, :end_ts_sp, :target_states, :target_regions, :categories, :priority)
        ON DUPLICATE KEY UPDATE name=VALUES(name), start_ts_sp=VALUES(start_ts_sp), end_ts_sp=VALUES(end_ts_sp),
            target_states=VALUES(target_states), target_regions=VALUES(target_regions),
            categories=VALUES(categories), priority=VALUES(priority)
    """
    return _execute_many(conn, sql, records)


def upsert_dim_date(conn: Connection, df: pd.DataFrame) -> int:
    sql = """
        INSERT INTO dim_date (date_key, year_num, month_num, day_num, day_of_week, is_weekend, is_national_holiday)
        VALUES (:date_key, :year_num, :month_num, :day_num, :day_of_week, :is_weekend, :is_national_holiday)
        ON DUPLICATE KEY UPDATE is_national_holiday=VALUES(is_national_holiday)
    """
    return _execute_many(conn, sql, df_to_records(df))


# ========================= reference tables =========================

def upsert_ref_fx_rate(conn: Connection, df: pd.DataFrame) -> int:
    sql = """
        INSERT INTO ref_fx_rate (rate_date, brl_to_usd) VALUES (:rate_date, :brl_to_usd)
        ON DUPLICATE KEY UPDATE brl_to_usd=VALUES(brl_to_usd)
    """
    return _execute_many(conn, sql, df_to_records(df))


def upsert_ref_holiday(conn: Connection, df: pd.DataFrame) -> int:
    sql = """
        INSERT INTO ref_holiday (holiday_date, state_code, name) VALUES (:holiday_date, :state_code, :name)
        ON DUPLICATE KEY UPDATE name=VALUES(name)
    """
    return _execute_many(conn, sql, df_to_records(df))


def upsert_ref_state(conn: Connection, df: pd.DataFrame) -> int:
    sql = """
        INSERT INTO ref_state (state_code, state_name, region) VALUES (:state_code, :state_name, :region)
        ON DUPLICATE KEY UPDATE state_name=VALUES(state_name), region=VALUES(region)
    """
    return _execute_many(conn, sql, df_to_records(df))


def reload_ref_commission_rate(conn: Connection, df: pd.DataFrame) -> int:
    conn.execute(text("DELETE FROM ref_commission_rate"))
    sql = """
        INSERT INTO ref_commission_rate (tier, rate, effective_from, effective_to)
        VALUES (:tier, :rate, :effective_from, :effective_to)
    """
    return _execute_many(conn, sql, df_to_records(df))


def reload_ref_category_override(conn: Connection, df: pd.DataFrame) -> int:
    conn.execute(text("DELETE FROM ref_category_override"))
    sql = """
        INSERT INTO ref_category_override (category_name_en, fixed_rate, effective_from, effective_to, notes)
        VALUES (:category_name_en, :fixed_rate, :effective_from, :effective_to, :notes)
    """
    return _execute_many(conn, sql, df_to_records(df))


# ========================= facts (delete-then-insert by range) =========================

def replace_facts_for_range(
    conn: Connection,
    start_date: str,
    end_date: str,
    fact_order_df: pd.DataFrame,
    fact_order_item_df: pd.DataFrame,
    fact_order_seller_df: pd.DataFrame,
) -> dict[str, int]:
    order_ids = conn.execute(
        text("SELECT order_id FROM fact_order WHERE purchase_ts >= :start AND purchase_ts < :end_excl"),
        {"start": start_date, "end_excl": str(pd.Timestamp(end_date) + pd.Timedelta(days=1))},
    ).scalars().all()

    if order_ids:
        # Chunked IN-deletes to avoid overly long parameter lists.
        for i in range(0, len(order_ids), 2000):
            chunk = order_ids[i : i + 2000]
            placeholders = ", ".join(f":id{j}" for j in range(len(chunk)))
            params = {f"id{j}": oid for j, oid in enumerate(chunk)}
            conn.execute(text(f"DELETE FROM fact_order_item WHERE order_id IN ({placeholders})"), params)
            conn.execute(text(f"DELETE FROM fact_order_seller WHERE order_id IN ({placeholders})"), params)
            conn.execute(text(f"DELETE FROM fact_order WHERE order_id IN ({placeholders})"), params)

    insert_order_sql = """
        INSERT INTO fact_order (order_id, customer_unique_id, order_status, customer_state_code, customer_region,
            is_revenue_eligible, has_broken_delivery,
            purchase_ts, approved_at, delivered_carrier_date, delivered_customer_date, estimated_delivery_date,
            gross_brl, gross_usd, net_cash_paid_brl, payment_total_brl, payment_mismatch, fx_rate, fx_rate_date,
            on_time, delivery_business_days, review_score, run_id)
        VALUES (:order_id, :customer_unique_id, :order_status, :customer_state_code, :customer_region,
            :is_revenue_eligible, :has_broken_delivery,
            :purchase_ts, :approved_at, :delivered_carrier_date, :delivered_customer_date, :estimated_delivery_date,
            :gross_brl, :gross_usd, :net_cash_paid_brl, :payment_total_brl, :payment_mismatch, :fx_rate, :fx_rate_date,
            :on_time, :delivery_business_days, :review_score, :run_id)
    """
    n_orders = _execute_many(conn, insert_order_sql, df_to_records(fact_order_df))

    insert_item_sql = """
        INSERT INTO fact_order_item (order_id, order_item_id, seller_id, product_id, price_brl, freight_value_brl,
            item_gross_brl, item_gross_usd, fx_rate, fx_rate_date, category_name_en, seller_tier_at_purchase,
            commission_rate, commission_brl, commission_usd, shipping_limit_date, distance_km, campaign_id,
            purchase_month, run_id)
        VALUES (:order_id, :order_item_id, :seller_id, :product_id, :price_brl, :freight_value_brl,
            :item_gross_brl, :item_gross_usd, :fx_rate, :fx_rate_date, :category_name_en, :seller_tier_at_purchase,
            :commission_rate, :commission_brl, :commission_usd, :shipping_limit_date, :distance_km, :campaign_id,
            :purchase_month, :run_id)
    """
    n_items = _execute_many(conn, insert_item_sql, df_to_records(fact_order_item_df))

    insert_seller_sql = """
        INSERT INTO fact_order_seller (order_id, seller_id, earliest_shipping_limit, lateness_status,
            review_score, purchase_month, run_id)
        VALUES (:order_id, :seller_id, :earliest_shipping_limit, :lateness_status, :review_score,
            :purchase_month, :run_id)
    """
    n_sellers = _execute_many(conn, insert_seller_sql, df_to_records(fact_order_seller_df))

    return {"fact_order": n_orders, "fact_order_item": n_items, "fact_order_seller": n_sellers}


# ========================= data quality =========================

def insert_dq_issues(conn: Connection, issues: list[dict], run_id: int) -> int:
    if not issues:
        return 0
    records = [{**issue, "detected_at": dt.datetime.now(), "run_id": run_id} for issue in issues]
    sql = """
        INSERT INTO data_quality_issue (rule_id, entity_type, entity_id, issue_type, details, detected_at, run_id)
        VALUES (:rule_id, :entity_type, :entity_id, :issue_type, :details, :detected_at, :run_id)
    """
    return _execute_many(conn, sql, records)
