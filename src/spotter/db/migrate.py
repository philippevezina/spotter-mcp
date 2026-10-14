"""Apply Alembic migrations up to head. Run by `spotter migrate`, which the production
build calls before each deploy (decisions.md, "Migrations on deploy").

One transaction under an advisory lock, so two overlapping builds never migrate at once
and a failed migration leaves the schema as it was.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, func, select

# Any constant distinct from the sync lock (spotter.sync.sync.LOCK_KEY).
LOCK_KEY = 0x5307_0001
SCRIPTS = Path(__file__).resolve().parent.parent / "migrations"


def upgrade_head(engine: Engine) -> tuple[str | None, str | None]:
    """Migrate to head. Returns the revision before and after."""
    cfg = Config()
    cfg.set_main_option("script_location", str(SCRIPTS))
    with engine.begin() as conn:
        conn.execute(select(func.pg_advisory_xact_lock(LOCK_KEY)))
        before = MigrationContext.configure(conn).get_current_revision()
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")
        after = MigrationContext.configure(conn).get_current_revision()
    return before, after
