from __future__ import annotations

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from pipeline.api.routers import campaigns, dq, health, kpis, orders, sellers

app = FastAPI(
    title="Marketplace Seller Performance API",
    description="Read-only API over the pipeline's MySQL tables for Finance/Ops dashboards.",
    version="1.0.0",
)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


app.include_router(health.router)
app.include_router(orders.router)
app.include_router(sellers.router)
app.include_router(kpis.router)
app.include_router(campaigns.router)
app.include_router(dq.router)
