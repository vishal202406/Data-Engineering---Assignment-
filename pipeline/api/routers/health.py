from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.engine import Connection

from pipeline.api.deps import db_ok, get_conn
from pipeline.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(conn: Connection = Depends(get_conn)) -> HealthResponse:
    ok = db_ok(conn)
    return HealthResponse(status="ok" if ok else "degraded", database="connected" if ok else "unreachable")
