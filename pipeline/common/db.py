from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker

from pipeline.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(settings.sqlalchemy_url, pool_pre_ping=True, future=True)


def get_session_factory():
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
