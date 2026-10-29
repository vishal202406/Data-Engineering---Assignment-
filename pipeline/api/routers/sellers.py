from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.api.deps import DEFAULT_LIMIT, clamp_limit, get_conn
from pipeline.api.schemas import SellerScorecardOut, SellerTopEntryOut, SellerTopOut

router = APIRouter(tags=["sellers"])

MONTH_DASH_RE = re.compile(r"^\d{4}-\d{2}$")
MONTH_COMPACT_RE = re.compile(r"^\d{6}$")
VALID_METRICS = {"commission_usd", "on_time_rate", "avg_review"}


@router.get("/sellers/{seller_id}/scorecard", response_model=SellerScorecardOut)
def seller_scorecard(seller_id: str, month: str = Query(...), conn: Connection = Depends(get_conn)) -> SellerScorecardOut:
    if not MONTH_DASH_RE.match(month):
        raise HTTPException(status_code=422, detail="month must be in YYYY-MM format")

    seller_exists = conn.execute(
        text("SELECT 1 FROM dim_seller WHERE seller_id = :seller_id"), {"seller_id": seller_id}
    ).first()
    if seller_exists is None:
        raise HTTPException(status_code=404, detail=f"Seller '{seller_id}' not found")

    item_row = conn.execute(
        text(
            "SELECT MAX(seller_tier_at_purchase) AS tier, COUNT(*) AS item_count, "
            "COALESCE(SUM(item_gross_brl),0) AS gross_brl, COALESCE(SUM(item_gross_usd),0) AS gross_usd, "
            "COALESCE(SUM(commission_brl),0) AS commission_brl, COALESCE(SUM(commission_usd),0) AS commission_usd "
            "FROM fact_order_item WHERE seller_id = :seller_id AND purchase_month = :month"
        ),
        {"seller_id": seller_id, "month": month},
    ).mappings().first()

    sla_row = conn.execute(
        text(
            "SELECT "
            "SUM(CASE WHEN lateness_status = 'on_time' THEN 1 ELSE 0 END) AS on_time_n, "
            "SUM(CASE WHEN lateness_status IS NOT NULL THEN 1 ELSE 0 END) AS known_n, "
            "SUM(CASE WHEN lateness_status = 'seller_late' THEN 1 ELSE 0 END) AS seller_late_n, "
            "AVG(review_score) AS avg_review "
            "FROM fact_order_seller WHERE seller_id = :seller_id AND purchase_month = :month"
        ),
        {"seller_id": seller_id, "month": month},
    ).mappings().first()

    on_time_pct = None
    if sla_row["known_n"]:
        on_time_pct = round(100.0 * sla_row["on_time_n"] / sla_row["known_n"], 2)

    return SellerScorecardOut(
        seller_id=seller_id,
        month=month,
        tier=item_row["tier"],
        item_count=item_row["item_count"] or 0,
        gross_brl=item_row["gross_brl"],
        gross_usd=item_row["gross_usd"],
        commission_brl=item_row["commission_brl"],
        commission_usd=item_row["commission_usd"],
        on_time_pct=on_time_pct,
        seller_late_count=sla_row["seller_late_n"] or 0,
        avg_review_score=float(sla_row["avg_review"]) if sla_row["avg_review"] is not None else None,
    )


@router.get("/sellers/top", response_model=SellerTopOut)
def sellers_top(
    month: str = Query(...),
    metric: str = Query(...),
    limit: int = Query(DEFAULT_LIMIT),
    offset: int = Query(0, ge=0),
    conn: Connection = Depends(get_conn),
) -> SellerTopOut:
    if not MONTH_COMPACT_RE.match(month):
        raise HTTPException(status_code=422, detail="month must be in YYYYMM format")
    if metric not in VALID_METRICS:
        raise HTTPException(status_code=422, detail=f"metric must be one of {sorted(VALID_METRICS)}")
    limit = clamp_limit(limit)
    month_dash = f"{month[:4]}-{month[4:]}"

    if metric == "commission_usd":
        rows = conn.execute(
            text(
                "SELECT foi.seller_id AS seller_id, SUM(foi.commission_usd) AS value, "
                "COUNT(DISTINCT CASE WHEN fo.order_status = 'delivered' THEN foi.order_id END) AS delivered_orders "
                "FROM fact_order_item foi JOIN fact_order fo ON foi.order_id = fo.order_id "
                "WHERE foi.purchase_month = :month "
                "GROUP BY foi.seller_id ORDER BY value DESC LIMIT :limit OFFSET :offset"
            ),
            {"month": month_dash, "limit": limit, "offset": offset},
        ).mappings().all()
    else:
        order_col = "on_time_rate" if metric == "on_time_rate" else "avg_review"
        rows = conn.execute(
            text(
                "SELECT seller_id, on_time_rate AS on_time_rate, avg_review AS avg_review, delivered_orders FROM ("
                "  SELECT fos.seller_id AS seller_id, "
                "    SUM(CASE WHEN fos.lateness_status = 'on_time' THEN 1 ELSE 0 END) / "
                "      NULLIF(SUM(CASE WHEN fos.lateness_status IS NOT NULL THEN 1 ELSE 0 END), 0) AS on_time_rate, "
                "    AVG(fos.review_score) AS avg_review, "
                "    COUNT(DISTINCT CASE WHEN fo.order_status = 'delivered' THEN fos.order_id END) AS delivered_orders "
                "  FROM fact_order_seller fos JOIN fact_order fo ON fos.order_id = fo.order_id "
                "  WHERE fos.purchase_month = :month "
                "  GROUP BY fos.seller_id"
                ") ranked WHERE delivered_orders >= 10 "
                f"ORDER BY {order_col} DESC LIMIT :limit OFFSET :offset"
            ),
            {"month": month_dash, "limit": limit, "offset": offset},
        ).mappings().all()

    results = []
    for r in rows:
        value = r["value"] if metric == "commission_usd" else r[order_col]
        results.append(SellerTopEntryOut(
            seller_id=r["seller_id"],
            metric=metric,
            value=float(value) if value is not None else 0.0,
            delivered_orders=r["delivered_orders"] or 0,
        ))

    return SellerTopOut(month=month_dash, metric=metric, limit=limit, offset=offset, results=results)
