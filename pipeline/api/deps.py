from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import text
from sqlalchemy.engine import Connection

from pipeline.common.db import get_engine

MAX_LIMIT = 100
DEFAULT_LIMIT = 10


def get_conn() -> Generator[Connection, None, None]:
    engine = get_engine()
    with engine.connect() as conn:
        yield conn


def clamp_limit(limit: int) -> int:
    return max(1, min(limit, MAX_LIMIT))


def db_ok(conn: Connection) -> bool:
    try:
        conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
