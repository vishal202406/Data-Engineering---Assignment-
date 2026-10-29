from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.api.deps import get_conn
from pipeline.api.schemas import DQIssueOut, OrderDetailOut, OrderItemOut, OrderSellerOut

router = APIRouter(tags=["orders"])


@router.get("/orders/{order_id}", response_model=OrderDetailOut)
def get_order(order_id: str, conn: Connection = Depends(get_conn)) -> OrderDetailOut:
    order_row = conn.execute(
        text("SELECT * FROM fact_order WHERE order_id = :order_id"), {"order_id": order_id}
    ).mappings().first()
    if order_row is None:
        raise HTTPException(status_code=404, detail=f"Order '{order_id}' not found")

    items = conn.execute(
        text(
            "SELECT order_item_id, seller_id, product_id, category_name_en, price_brl, freight_value_brl, "
            "item_gross_brl, item_gross_usd, commission_rate, commission_brl, commission_usd, campaign_id, "
            "distance_km FROM fact_order_item WHERE order_id = :order_id ORDER BY order_item_id"
        ),
        {"order_id": order_id},
    ).mappings().all()

    sellers = conn.execute(
        text(
            "SELECT seller_id, lateness_status, review_score FROM fact_order_seller "
            "WHERE order_id = :order_id ORDER BY seller_id"
        ),
        {"order_id": order_id},
    ).mappings().all()

    dq_issues = conn.execute(
        text(
            "SELECT rule_id, issue_type, details FROM data_quality_issue "
            "WHERE entity_type = 'order' AND entity_id = :order_id "
            "OR entity_type = 'order_item' AND entity_id LIKE :order_item_pattern "
            "ORDER BY detected_at DESC"
        ),
        {"order_id": order_id, "order_item_pattern": f"{order_id}:%"},
    ).mappings().all()

    return OrderDetailOut(
        order_id=order_row["order_id"],
        customer_unique_id=order_row["customer_unique_id"],
        order_status=order_row["order_status"],
        is_revenue_eligible=bool(order_row["is_revenue_eligible"]),
        purchase_ts=order_row["purchase_ts"],
        approved_at=order_row["approved_at"],
        delivered_carrier_date=order_row["delivered_carrier_date"],
        delivered_customer_date=order_row["delivered_customer_date"],
        estimated_delivery_date=order_row["estimated_delivery_date"],
        gross_brl=order_row["gross_brl"],
        gross_usd=order_row["gross_usd"],
        fx_rate=order_row["fx_rate"],
        fx_rate_date=order_row["fx_rate_date"],
        on_time=None if order_row["on_time"] is None else bool(order_row["on_time"]),
        delivery_business_days=order_row["delivery_business_days"],
        review_score=order_row["review_score"],
        items=[OrderItemOut(**dict(r)) for r in items],
        sellers=[OrderSellerOut(**dict(r)) for r in sellers],
        dq_issues=[DQIssueOut(**dict(r)) for r in dq_issues],
    )
