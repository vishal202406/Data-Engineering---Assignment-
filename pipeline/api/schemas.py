from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    database: str


class OrderItemOut(BaseModel):
    order_item_id: int
    seller_id: str
    product_id: str
    category_name_en: str | None
    price_brl: Decimal
    freight_value_brl: Decimal
    item_gross_brl: Decimal
    item_gross_usd: Decimal | None
    commission_rate: Decimal | None
    commission_brl: Decimal | None
    commission_usd: Decimal | None
    campaign_id: str | None
    distance_km: Decimal | None


class OrderSellerOut(BaseModel):
    seller_id: str
    lateness_status: str | None
    review_score: int | None


class DQIssueOut(BaseModel):
    rule_id: str
    issue_type: str
    details: str | None


class OrderDetailOut(BaseModel):
    order_id: str
    customer_unique_id: str
    order_status: str
    is_revenue_eligible: bool
    purchase_ts: dt.datetime
    approved_at: dt.datetime | None
    delivered_carrier_date: dt.datetime | None
    delivered_customer_date: dt.datetime | None
    estimated_delivery_date: dt.date | None
    gross_brl: Decimal
    gross_usd: Decimal
    fx_rate: Decimal | None
    fx_rate_date: dt.date | None
    on_time: bool | None
    delivery_business_days: int | None
    review_score: int | None
    items: list[OrderItemOut]
    sellers: list[OrderSellerOut]
    dq_issues: list[DQIssueOut]


class SellerScorecardOut(BaseModel):
    seller_id: str
    month: str
    tier: str | None
    item_count: int
    gross_brl: Decimal
    gross_usd: Decimal
    commission_brl: Decimal
    commission_usd: Decimal
    on_time_pct: float | None
    seller_late_count: int
    avg_review_score: float | None


class SellerTopEntryOut(BaseModel):
    seller_id: str
    metric: str
    value: float
    delivered_orders: int


class SellerTopOut(BaseModel):
    month: str
    metric: str
    limit: int
    offset: int
    results: list[SellerTopEntryOut]


class KpiMonthlyEntryOut(BaseModel):
    month: str
    orders: int
    unique_customers: int
    gross_usd: Decimal
    net_cash_paid_brl: Decimal
    on_time_pct: float | None
    avg_delivery_business_days: float | None


class KpiMonthlyOut(BaseModel):
    region: str | None
    results: list[KpiMonthlyEntryOut]


class CampaignWindowOut(BaseModel):
    items: int
    orders: int
    gross_usd: Decimal
    avg_order_value_usd: Decimal | None


class CampaignPerformanceOut(BaseModel):
    campaign_id: str
    name: str
    campaign_window: CampaignWindowOut
    comparison_window: CampaignWindowOut


class DQSummaryEntryOut(BaseModel):
    rule_id: str
    issue_type: str
    count: int


class DQSummaryOut(BaseModel):
    run_id: int
    limit: int
    offset: int
    results: list[DQSummaryEntryOut]
