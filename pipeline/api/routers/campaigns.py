from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.api.deps import get_conn
from pipeline.api.schemas import CampaignPerformanceOut, CampaignWindowOut

router = APIRouter(tags=["campaigns"])


def _window_stats(conn: Connection, campaign_id: str | None, start=None, end=None) -> CampaignWindowOut:
    if campaign_id is not None:
        row = conn.execute(
            text(
                "SELECT COUNT(*) AS items, COUNT(DISTINCT order_id) AS orders, "
                "COALESCE(SUM(item_gross_usd), 0) AS gross_usd "
                "FROM fact_order_item WHERE campaign_id = :campaign_id"
            ),
            {"campaign_id": campaign_id},
        ).mappings().first()
    else:
        row = conn.execute(
            text(
                "SELECT COUNT(*) AS items, COUNT(DISTINCT foi.order_id) AS orders, "
                "COALESCE(SUM(foi.item_gross_usd), 0) AS gross_usd "
                "FROM fact_order_item foi JOIN fact_order fo ON foi.order_id = fo.order_id "
                "WHERE fo.purchase_ts >= :start AND fo.purchase_ts < :end"
            ),
            {"start": start, "end": end},
        ).mappings().first()

    orders = row["orders"] or 0
    avg_order_value = float(row["gross_usd"]) / orders if orders else None
    return CampaignWindowOut(
        items=row["items"] or 0, orders=orders, gross_usd=row["gross_usd"],
        avg_order_value_usd=avg_order_value,
    )


@router.get("/campaigns/{campaign_id}/performance", response_model=CampaignPerformanceOut)
def campaign_performance(campaign_id: str, conn: Connection = Depends(get_conn)) -> CampaignPerformanceOut:
    campaign = conn.execute(
        text("SELECT campaign_id, name, start_ts_sp, end_ts_sp FROM dim_campaign WHERE campaign_id = :id"),
        {"id": campaign_id},
    ).mappings().first()
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"Campaign '{campaign_id}' not found")

    duration = campaign["end_ts_sp"] - campaign["start_ts_sp"]
    comp_end = campaign["start_ts_sp"]
    comp_start = comp_end - duration

    campaign_window = _window_stats(conn, campaign_id)
    comparison_window = _window_stats(conn, None, start=comp_start, end=comp_end)

    return CampaignPerformanceOut(
        campaign_id=campaign["campaign_id"], name=campaign["name"],
        campaign_window=campaign_window, comparison_window=comparison_window,
    )
