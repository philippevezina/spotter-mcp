"""Incremental Garmin sync (SPEC section 9): activities of every type, strength sets, stats.

- One run at a time: a session advisory lock held on a dedicated connection.
  Pass an unpooled engine as `lock_engine`; Neon's pooler is in transaction mode.
- Each strength activity commits with its sets and stats, so a failed or
  partial run keeps what it did. An activity row with type strength_training
  therefore always has its sets.
- The cursor moves only when a run completes.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import Connection, Engine, delete, func, select
from sqlalchemy.dialects.postgresql import insert

from spotter.db.schema import activities, athlete, exercises, performed_sets, sync_state
from spotter.garmin import mappers
from spotter.sync.stats import rebuild_activity_stats

SOURCE = "activities"
LOCK_KEY = 0x5907_7E12  # arbitrary, unique to Spotter's sync
DEFAULT_TZ = "America/Toronto"
MIN_INTERVAL = timedelta(minutes=10)  # SPEC 8.5, unless force
OVERLAP_DAYS = 2  # re-read recent strength sets to catch edits made in the app
DEFAULT_LOOKBACK_DAYS = 28  # first sync without a backfill
DEFAULT_BUDGET_S = 60.0


class ActivitySource(Protocol):
    def activities_by_date(self, start: date, end: date) -> list[dict[str, Any]]: ...

    def exercise_sets(self, activity_id: int) -> dict[str, Any]: ...


@dataclass
class SyncResult:
    skipped: str | None = None  # "locked" | "recent"
    start: date | None = None
    end: date | None = None
    activities: int = 0
    strength_sessions: int = 0
    unmapped_sets: int = 0
    partial: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {k: v.isoformat() if isinstance(v, date) else v for k, v in asdict(self).items()}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def athlete_tz(conn: Connection) -> ZoneInfo:
    name = conn.execute(select(athlete.c.time_zone).where(athlete.c.id == 1)).scalar()
    return ZoneInfo(name or DEFAULT_TZ)


def _upsert_activity(conn: Connection, row: dict[str, Any]) -> None:
    stmt = insert(activities).values(**row)
    updates = {k: stmt.excluded[k] for k in row if k != "garmin_activity_id"}
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["garmin_activity_id"], set_={**updates, "synced_at": func.now()}
        )
    )


def _exercise_ids(conn: Connection) -> dict[tuple[str, str], int]:
    rows = conn.execute(
        select(exercises.c.id, exercises.c.garmin_category, exercises.c.garmin_name)
    )
    return {(r.garmin_category, r.garmin_name): r.id for r in rows}


def _store_strength(
    conn: Connection,
    row: dict[str, Any],
    payload: dict[str, Any],
    exercise_ids: dict[tuple[str, str], int],
) -> int:
    """Activity, replace-all sets, stats. Returns the number of unmapped sets."""
    _upsert_activity(conn, row)
    activity_id = row["garmin_activity_id"]
    conn.execute(delete(performed_sets).where(performed_sets.c.activity_id == activity_id))
    sets = mappers.map_exercise_sets(payload)
    unmapped = 0
    for s in sets:
        s["activity_id"] = activity_id
        s["exercise_id"] = exercise_ids.get((s["garmin_category"], s["garmin_name"]))
        unmapped += s["exercise_id"] is None
    if sets:
        conn.execute(insert(performed_sets), sets)
    rebuild_activity_stats(conn, activity_id)
    return unmapped


def _window_start(cursor: dict[str, Any] | None, since: date | None, today: date) -> date:
    if since is not None:
        return since
    if cursor and cursor.get("synced_through"):
        return date.fromisoformat(cursor["synced_through"]) - timedelta(days=OVERLAP_DAYS)
    return today - timedelta(days=DEFAULT_LOOKBACK_DAYS)


def run_sync(
    engine: Engine,
    client: ActivitySource,
    *,
    since: date | None = None,
    full: bool = False,
    force: bool = False,
    budget_s: float | None = DEFAULT_BUDGET_S,
    lock_engine: Engine | None = None,
    clock: Callable[[], float] = time.monotonic,
    now: Callable[[], datetime] = _utcnow,
) -> SyncResult:
    """Sync activities into Postgres. `budget_s=None` means no time limit (backfill).

    `full` re-reads sets of every strength activity in the window.
    """
    with (lock_engine or engine).connect() as lock_conn:
        got = lock_conn.execute(select(func.pg_try_advisory_lock(LOCK_KEY))).scalar_one()
        lock_conn.commit()
        if not got:
            return SyncResult(skipped="locked")
        try:
            return _run_locked(engine, client, since, full, force, budget_s, clock, now)
        finally:
            lock_conn.execute(select(func.pg_advisory_unlock(LOCK_KEY)))
            lock_conn.commit()


def _run_locked(
    engine: Engine,
    client: ActivitySource,
    since: date | None,
    full: bool,
    force: bool,
    budget_s: float | None,
    clock: Callable[[], float],
    now: Callable[[], datetime],
) -> SyncResult:
    started = now()
    with engine.connect() as conn:
        state = conn.execute(select(sync_state).where(sync_state.c.source == SOURCE)).first()
        tz = athlete_tz(conn)
        exercise_ids = _exercise_ids(conn)
    if (
        not force
        and state
        and state.last_synced_at
        and started - state.last_synced_at < MIN_INTERVAL
    ):
        return SyncResult(skipped="recent")

    today = started.astimezone(tz).date()
    result = SyncResult(
        start=_window_start(state.cursor if state else None, since, today), end=today
    )
    assert result.start is not None
    refetch_from = today - timedelta(days=OVERLAP_DAYS)
    deadline = None if budget_s is None else clock() + budget_s

    listing = client.activities_by_date(result.start, today)
    listing.sort(key=lambda s: s["startTimeGMT"], reverse=True)
    with engine.connect() as conn:
        known = set(
            conn.execute(
                select(activities.c.garmin_activity_id).where(
                    activities.c.garmin_activity_id.in_([int(s["activityId"]) for s in listing])
                )
            ).scalars()
        )

    for summary in listing:
        row = mappers.map_activity(summary, tz)
        is_known = row["garmin_activity_id"] in known
        needs_sets = mappers.is_strength(summary) and (
            full or not is_known or row["local_date"] >= refetch_from
        )
        if needs_sets and deadline is not None and clock() >= deadline:
            result.partial = True
            needs_sets = False
            if not is_known:
                continue  # never store a strength activity without its sets
        if needs_sets:
            payload = client.exercise_sets(row["garmin_activity_id"])
            with engine.begin() as conn:
                result.unmapped_sets += _store_strength(conn, row, payload, exercise_ids)
            result.strength_sessions += 1
        else:
            with engine.begin() as conn:
                _upsert_activity(conn, row)
        result.activities += 1

    if not result.partial:
        with engine.begin() as conn:
            stmt = insert(sync_state).values(
                source=SOURCE,
                last_synced_at=started,
                cursor={"synced_through": today.isoformat()},
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
