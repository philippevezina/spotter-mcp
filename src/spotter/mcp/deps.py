"""What tools need from the outside world: engines, a Garmin session factory and a clock.

Tests build `Deps` with the test engine, a fake Garmin session and a fixed clock.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.pool import NullPool

from spotter.config import Settings
from spotter.garmin.session import GarminSession, garmin_session
from spotter.sync.sync import athlete_tz


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Deps:
    engine: Engine
    # Sync's session advisory lock needs a direct connection: Neon's pooler is in
    # transaction mode (decisions.md, "Sync mechanics").
    lock_engine: Engine
    garmin: Callable[[Engine], AbstractContextManager[GarminSession]] = garmin_session
    now: Callable[[], datetime] = field(default=_utcnow)

    @classmethod
    def from_settings(cls, settings: Settings) -> Deps:
        return cls(
            engine=create_engine(settings.db_url, pool_pre_ping=True, pool_size=2),
            # NullPool: no idle session lingers between serverless invocations.
            lock_engine=create_engine(settings.migration_url, poolclass=NullPool),
        )

    def today(self, conn: Connection) -> date:
        """Today in the athlete's time zone. Vercel's clock is UTC (decisions.md)."""
        return self.now().astimezone(athlete_tz(conn)).date()
