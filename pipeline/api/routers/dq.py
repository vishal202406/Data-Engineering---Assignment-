from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.api.deps import DEFAULT_LIMIT, clamp_limit, get_conn
from pipeline.api.schemas import DQSummaryEntryOut, DQSummaryOut

router = APIRouter(tags=["data-quality"])


@router.get("/data-quality/summary", response_model=DQSummaryOut)
def dq_summary(
    run_id: int | None = Query(None),
    limit: int = Query(DEFAULT_LIMIT),
    offset: int = Query(0, ge=0),
    conn: Connection = Depends(get_conn),
) -> DQSummaryOut:
    limit = clamp_limit(limit)

    if run_id is None:
        latest = conn.execute(text("SELECT MAX(run_id) FROM pipeline_run")).scalar()
        if latest is None:
            raise HTTPException(status_code=404, detail="No pipeline runs recorded yet")
        run_id = latest
    else:
        exists = conn.execute(text("SELECT 1 FROM pipeline_run WHERE run_id = :id"), {"id": run_id}).first()
        if exists is None:
            raise HTTPException(status_code=404, detail=f"run_id {run_id} not found")

    rows = conn.execute(
        text(
            "SELECT rule_id, issue_type, COUNT(*) AS count FROM data_quality_issue "
            "WHERE run_id = :run_id GROUP BY rule_id, issue_type "
            "ORDER BY count DESC LIMIT :limit OFFSET :offset"
        ),
        {"run_id": run_id, "limit": limit, "offset": offset},
    ).mappings().all()

    return DQSummaryOut(
        run_id=run_id, limit=limit, offset=offset,
        results=[DQSummaryEntryOut(**dict(r)) for r in rows],
    )
