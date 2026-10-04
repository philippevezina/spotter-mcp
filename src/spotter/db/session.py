"""Engine creation. Callers own connections and transactions."""

from __future__ import annotations

from sqlalchemy import Engine, create_engine

from spotter.config import get_settings


def get_engine(url: str | None = None) -> Engine:
    return create_engine(url or get_settings().db_url, pool_pre_ping=True)
