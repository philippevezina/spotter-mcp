"""Seed reference data."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert

from spotter.db.schema import exercises
from spotter.garmin.client import CatalogEntry


def seed_exercises(conn: Connection, catalog: Iterable[CatalogEntry]) -> int:
    """Insert catalog rows that are not there yet. Returns the number inserted.

    Existing rows are left alone, so curated fields are never overwritten.
    """
    rows = [
        {"display_name": e.display_name, "garmin_category": e.category, "garmin_name": e.name}
        for e in catalog
    ]
    if not rows:
        return 0
    stmt = (
        insert(exercises)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["garmin_category", "garmin_name"])
        .returning(exercises.c.id)
    )
    return len(conn.execute(stmt).all())
