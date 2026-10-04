from __future__ import annotations

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError

from spotter.db.schema import metadata
from tests.conftest import run_alembic


def test_schema_matches_migrations(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == []


def test_downgrade_then_upgrade(engine: Engine) -> None:
    run_alembic(engine, "downgrade", "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    run_alembic(engine, "upgrade", "head")
    assert set(metadata.tables) <= set(inspect(engine).get_table_names())


def test_no_kv_store_table(engine: Engine) -> None:
    assert "kv_store" not in inspect(engine).get_table_names()


def test_only_one_active_plan(db: Engine) -> None:
    insert_plan = text(
        "INSERT INTO plans (name, status, weeks, sessions_per_week, progression_model,"
        " end_criteria, rationale) VALUES (:n, :s, 6, 3, 'linear', '{}', 'r')"
    )
    with db.begin() as conn:
        conn.execute(insert_plan, {"n": "a", "s": "active"})
        conn.execute(insert_plan, {"n": "b", "s": "draft"})
        conn.execute(insert_plan, {"n": "c", "s": "draft"})
    with db.connect() as conn, pytest.raises(IntegrityError, match="one_active_plan"):
        conn.execute(insert_plan, {"n": "d", "s": "active"})


def test_single_row_tables(db: Engine) -> None:
    with db.connect() as conn, pytest.raises(IntegrityError, match="ck_athlete_single_row"):
        conn.execute(text("INSERT INTO athlete (id) VALUES (2)"))
