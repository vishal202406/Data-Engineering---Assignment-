from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.api.deps import get_conn
from pipeline.api.schemas import KpiMonthlyEntryOut, KpiMonthlyOut

router = APIRouter(tags=["kpis"])

MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


@router.get("/kpis/monthly", response_model=KpiMonthlyOut)
def kpis_monthly(
    start: str = Query(...),
    end: str = Query(...),
    region: str | None = Query(None),
    conn: Connection = Depends(get_conn),
) -> KpiMonthlyOut:
    if not MONTH_RE.match(start) or not MONTH_RE.match(end):
        raise HTTPException(status_code=422, detail="start and end must be in YYYY-MM format")
    if start > end:
        raise HTTPException(status_code=422, detail="start must be <= end")

    start_ts = f"{start}-01"
    end_ts = f"{end}-28"  # widened below via DATE_ADD to cover any month length

    params = {"start_ts": start_ts, "end_month": end}
    region_clause = ""
    if region is not None:
        region_clause = "AND customer_region = :region"
        params["region"] = region

    rows = conn.execute(
        text(
            "SELECT DATE_FORMAT(purchase_ts, '%Y-%m') AS month, "
            "COUNT(*) AS orders, "
            "COUNT(DISTINCT customer_unique_id) AS unique_customers, "
            "COALESCE(SUM(gross_usd), 0) AS gross_usd, "
            "COALESCE(SUM(net_cash_paid_brl), 0) AS net_cash_paid_brl, "
            "AVG(CASE WHEN on_time IS NOT NULL THEN on_time END) * 100 AS on_time_pct, "
            "AVG(delivery_business_days) AS avg_delivery_business_days "
            "FROM fact_order "
            f"WHERE purchase_ts >= :start_ts AND DATE_FORMAT(purchase_ts, '%Y-%m') <= :end_month {region_clause} "
            "GROUP BY month ORDER BY month"
        ),
        params,
    ).mappings().all()

    results = [
        KpiMonthlyEntryOut(
            month=r["month"],
            orders=r["orders"],
            unique_customers=r["unique_customers"],
            gross_usd=r["gross_usd"],
            net_cash_paid_brl=r["net_cash_paid_brl"],
            on_time_pct=round(float(r["on_time_pct"]), 2) if r["on_time_pct"] is not None else None,
            avg_delivery_business_days=round(float(r["avg_delivery_business_days"]), 2)
            if r["avg_delivery_business_days"] is not None else None,
        )
        for r in rows
    ]
    return KpiMonthlyOut(region=region, results=results)
