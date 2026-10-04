"""Test database: a fresh `spotter_test` on the compose Postgres, migrated once per session.

Start it with `docker compose up -d db`. Override with TEST_DATABASE_URL.
Tests never touch the real Garmin API.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError

from spotter.config import get_settings, sqlalchemy_url
from spotter.db.schema import metadata

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEST_URL = "postgresql://spotter:spotter@localhost:5432/spotter_test"


def _test_url() -> str:
    return sqlalchemy_url(os.environ.get("TEST_DATABASE_URL", DEFAULT_TEST_URL))


def _recreate_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{target.database}" WITH (FORCE)'))
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    except OperationalError as exc:
        pytest.exit(
            f"Test Postgres unreachable at {target.render_as_string(hide_password=True)}. "
            f"Run `docker compose up -d db`. ({exc.orig})",
            returncode=2,
        )
    finally:
        admin.dispose()


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    return cfg


def run_alembic(engine: Engine, fn: str, revision: str) -> None:
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        getattr(command, fn)(cfg, revision)


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    url = _test_url()
    _recreate_database(url)
    eng = create_engine(url)
    run_alembic(eng, "upgrade", "head")
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Engine]:
    """Empty tables before each test."""
    names = ", ".join(f'"{t.name}"' for t in metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield engine


@pytest.fixture
def token_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def env(
    monkeypatch: pytest.MonkeyPatch, db: Engine, token_key: str
) -> Iterator[pytest.MonkeyPatch]:
    """Point spotter.config at the test database and a fresh token key."""
    monkeypatch.setenv("DATABASE_URL", db.url.render_as_string(hide_password=False))
    monkeypatch.delenv("DATABASE_URL_UNPOOLED", raising=False)
    monkeypatch.setenv("GARMIN_TOKEN_KEY", token_key)
    monkeypatch.chdir(ROOT / "tests")  # keep a developer's .env out of the way
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()
