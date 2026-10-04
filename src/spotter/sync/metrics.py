"""Daily metrics sync (SPEC section 9, decisions.md "Daily metrics sync window").

Runs inside `run_sync`, after activities, under the same lock and deadline.

- Range calls (HRV, max metrics, body composition) cover the whole window.
- Per-day calls (sleep, training readiness, daily summary) cover the last
  RECENT_DAYS days, plus any day in the window not fetched per-day before.
- Every day in the window gets a row. Upserts only touch the columns of the
  sources fetched, and merge `raw_json` per source, so range-only days keep
  their per-day values.
- When the budget runs out, the remaining days still get range data and the
  run reports `partial`; they are fetched per-day on the next run.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy import Connection, Engine, func, select
from sqlalchemy.dialects.postgresql import insert

from spotter.db.schema import daily_metrics, sync_state
from spotter.garmin import mappers

SOURCE = "daily_metrics"
RANGE_DAYS = 14  # each sync re-reads this many days: values get revised after sleep
RECENT_DAYS = 3  # per-day calls always re-read these days
BACKFILL_DAYS = 28  # the longest readiness window


class MetricsSource(Protocol):
    def hrv_range(self, start: date, end: date) -> dict[str, Any]: ...

    def max_metrics_range(self, start: date, end: date) -> Any: ...

    def body_composition_range(self, start: date, end: date) -> dict[str, Any]: ...

    def training_readiness(self, day: date) -> list[dict[str, Any]]: ...

    def sleep(self, day: date) -> dict[str, Any]: ...

    def daily_summary(self, day: date) -> dict[str, Any]: ...


@dataclass
class MetricsResult:
    days: int = 0  # rows written
    day_fetches: int = 0  # days fetched per-day
    partial: bool = False


def _fetched_per_day(conn: Connection, start: date, end: date) -> set[date]:
    rows = conn.execute(
        select(daily_metrics.c.local_date).where(
            daily_metrics.c.local_date.between(start, end),
            daily_metrics.c.raw_json.has_key("sleep"),
        )
    )
    return set(rows.scalars())


def _upsert(conn: Connection, row: dict[str, Any]) -> None:
    stmt = insert(daily_metrics).values(**row)
    updates = {k: stmt.excluded[k] for k in row if k not in ("local_date", "raw_json")}
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["local_date"],
            set_={
                **updates,
                "raw_json": daily_metrics.c.raw_json.concat(stmt.excluded.raw_json),
                "synced_at": func.now(),
            },
        )
    )


def _fetch_day(client: MetricsSource, day: date) -> dict[str, Any]:
    return {
        "sleep": client.sleep(day),
        "readiness": client.training_readiness(day),
        "summary": client.daily_summary(day),
    }


def sync_metrics(
    engine: Engine,
    client: MetricsSource,
    today: date,
    started: datetime,
    *,
    days: int = RANGE_DAYS,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> MetricsResult:
    """Sync `days` days ending `today` (athlete's local date). `deadline=None`: no budget."""
    start = today - timedelta(days=days - 1)
    hrv = mappers.index_hrv(client.hrv_range(start, today))
    max_metrics = mappers.index_max_metrics(client.max_metrics_range(start, today))
    weights = mappers.index_weights(client.body_composition_range(start, today))
    with engine.connect() as conn:
        done = _fetched_per_day(conn, start, today)
    recent_from = today - timedelta(days=RECENT_DAYS - 1)

    result = MetricsResult()
    for offset in range(days):  # newest first
        day = today - timedelta(days=offset)
        sources: dict[str, Any] = {
            "hrv": hrv.get(day),
            "max_metrics": max_metrics.get(day),
            "weight": weights.get(day),
        }
        if day >= recent_from or day not in done:
            if deadline is not None and clock() >= deadline:
                result.partial = True
            else:
                sources.update(_fetch_day(client, day))
                result.day_fetches += 1
        with engine.begin() as conn:
            _upsert(conn, mappers.map_daily_metrics(day, sources))
        result.days += 1

    if not result.partial:
        with engine.begin() as conn:
            stmt = insert(sync_state).values(
                source=SOURCE, last_synced_at=started, cursor={"synced_through": today.isoformat()}
            )
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["source"],
                    set_={
                        "last_synced_at": stmt.excluded.last_synced_at,
                        "cursor": stmt.excluded.cursor,
                    },
                )
            )
    return result
