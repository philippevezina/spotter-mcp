"""Sync against local Postgres with a fake Garmin client. No network."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, insert, select, text, update

from spotter.db.schema import (
    activities,
    exercise_aliases,
    exercise_session_stats,
    exercises,
    performed_sets,
    plan_days,
    plans,
    prescribed_sets,
    scheduled_sessions,
    sync_state,
)
from spotter.db.seed import seed_exercises
from spotter.garmin.client import CatalogEntry
from spotter.garmin.errors import GarminRateLimited
from spotter.mcp.tools.history import exposures
from spotter.sync import stats
from spotter.sync.sync import LOCK_KEY, link_sessions, run_sync
from spotter.units import lb_to_kg

FIXTURES = Path(__file__).parent / "fixtures" / "garmin"
NOW = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)  # noon in Toronto
BENCH = ("BENCH_PRESS", "BARBELL_BENCH_PRESS")


def _load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def strength(activity_id: int, start_gmt: str) -> dict[str, Any]:
    return {
        **_load("strength_activity_summary.json"),
        "activityId": activity_id,
        "startTimeGMT": start_gmt,
    }


def run(activity_id: int, start_gmt: str) -> dict[str, Any]:
    return {
        "activityId": activity_id,
        "activityType": {"typeKey": "running"},
        "startTimeGMT": start_gmt,
        "duration": 3600.0,
        "distance": 10000.0,
    }


class FakeClient:
    def __init__(self, summaries: list[dict[str, Any]]) -> None:
        self.summaries = summaries
        self.sets: dict[int, dict[str, Any]] = {}
        self.set_calls: list[int] = []
        self.windows: list[tuple[date, date]] = []
        self.fail_on: int | None = None
        for s in summaries:
            if s["activityType"]["typeKey"] == "strength_training":
                self.sets[s["activityId"]] = copy.deepcopy(_load("exercise_sets.json"))

    def activities_by_date(self, start: date, end: date) -> list[dict[str, Any]]:
        self.windows.append((start, end))
        return [copy.deepcopy(s) for s in self.summaries]

    def exercise_sets(self, activity_id: int) -> dict[str, Any]:
        if activity_id == self.fail_on:
            raise GarminRateLimited("429")
        self.set_calls.append(activity_id)
        return copy.deepcopy(self.sets[activity_id])

    # No daily metrics here; tests/test_metrics_sync.py covers them.
    def hrv_range(self, start: date, end: date) -> dict[str, Any]:
        return {}

    def max_metrics_range(self, start: date, end: date) -> list[Any]:
        return []

    def body_composition_range(self, start: date, end: date) -> dict[str, Any]:
        return {}

    def training_readiness(self, day: date) -> list[dict[str, Any]]:
        return []

    def sleep(self, day: date) -> dict[str, Any]:
        return {}

    def daily_summary(self, day: date) -> dict[str, Any]:
        return {}


@pytest.fixture
def catalog(db: Engine) -> Engine:
    with db.begin() as conn:
        seed_exercises(conn, [CatalogEntry("Barbell Bench Press", *BENCH)])
    return db


def history() -> FakeClient:
    """Old strength (outside overlap), a run, recent strength (inside overlap)."""
    return FakeClient(
        [
            strength(1, "2026-09-20 22:00:00"),
            run(2, "2026-09-25 11:00:00"),
            strength(3, "2026-10-03 22:00:00"),
        ]
    )


def count(db: Engine, table: Any) -> int:
    with db.connect() as conn:
        return conn.execute(select(func.count()).select_from(table)).scalar_one()


def sync(db: Engine, client: FakeClient, **kw: Any) -> Any:
    kw.setdefault("now", lambda: NOW)
    return run_sync(db, client, **kw)


def test_first_sync(catalog: Engine) -> None:
    client = history()
    result = sync(catalog, client, since=date(2026, 9, 1))
    assert result.as_dict() == {
        "skipped": None,
        "start": "2026-09-01",
        "end": "2026-10-04",
        "activities": 3,
        "strength_sessions": 2,
        "unmapped_sets": 0,
        "metric_days": 14,
        "metric_day_fetches": 14,
        "linked_sessions": 0,
        "partial": False,
    }
    assert client.set_calls == [3, 1]  # newest first
    assert count(catalog, performed_sets) == 4  # 2 ACTIVE sets each, REST dropped
    with catalog.connect() as conn:
        row = conn.execute(
            select(exercise_session_stats).where(exercise_session_stats.c.activity_id == 3)
        ).one()
        state = conn.execute(select(sync_state).where(sync_state.c.source == "activities")).one()
        local = conn.execute(
            select(activities.c.local_date).where(activities.c.garmin_activity_id == 3)
        ).scalar_one()
    # 190 lb x 5 plus a 0-rep set: one working set.
    assert (row.top_set_weight_kg, row.top_set_reps, row.working_sets) == (
        Decimal("86.187"),
        5,
        1,
    )
    assert (row.total_reps, row.volume_kg, row.best_e1rm_kg) == (5, Decimal("430.94"), None)
    assert local == date(2026, 10, 3)  # 22:00 GMT is 18:00 in Toronto
    assert state.cursor == {"synced_through": "2026-10-04"}
    assert state.last_synced_at == NOW


def test_rerun_is_idempotent_and_refetches_only_overlap(catalog: Engine) -> None:
    client = history()
    sync(catalog, client, since=date(2026, 9, 1))
    client.set_calls.clear()
    result = sync(catalog, client, force=True)
    assert client.set_calls == [3]  # 2026-10-03 is inside the 2-day overlap
    assert client.windows[-1] == (date(2026, 10, 2), date(2026, 10, 4))  # cursor - overlap
    assert result.activities == 3
    assert count(catalog, activities) == 3
    assert count(catalog, performed_sets) == 4
    assert count(catalog, exercise_session_stats) == 2


def test_full_refetches_everything(catalog: Engine) -> None:
    client = history()
    sync(catalog, client, since=date(2026, 9, 1))
    client.set_calls.clear()
    sync(catalog, client, since=date(2026, 9, 1), full=True, force=True)
    assert client.set_calls == [3, 1]


def test_overlap_refetch_replaces_sets(catalog: Engine) -> None:
    client = history()
    sync(catalog, client, since=date(2026, 9, 1))
    edited = client.sets[3]["exerciseSets"]
    edited[2]["repetitionCount"] = 4  # athlete fixes the 0-rep set in the app
    sync(catalog, client, force=True)
    with catalog.connect() as conn:
        reps = conn.execute(
            select(performed_sets.c.reps)
            .where(performed_sets.c.activity_id == 3)
            .order_by(performed_sets.c.set_index)
        ).scalars()
        working = conn.execute(
            select(exercise_session_stats.c.working_sets).where(
                exercise_session_stats.c.activity_id == 3
            )
        ).scalar_one()
    assert list(reps) == [5, 4]
    assert working == 2


def test_rate_limit_unless_forced(catalog: Engine) -> None:
    client = history()
    sync(catalog, client)
    assert sync(catalog, client, now=lambda: NOW + timedelta(minutes=9)).skipped == "recent"
    assert sync(catalog, client, now=lambda: NOW + timedelta(minutes=9), force=True).skipped is None
    assert sync(catalog, client, now=lambda: NOW + timedelta(minutes=20)).skipped is None


def test_first_sync_default_lookback(catalog: Engine) -> None:
    client = history()
    sync(catalog, client)
    assert client.windows == [(date(2026, 9, 6), date(2026, 10, 4))]


@pytest.fixture
def held_lock(db: Engine) -> Iterator[None]:
    with db.connect() as other:
        other.execute(select(func.pg_advisory_lock(LOCK_KEY)))
        yield
        other.execute(select(func.pg_advisory_unlock(LOCK_KEY)))


def test_skips_when_locked(catalog: Engine, held_lock: None) -> None:
    client = history()
    assert sync(catalog, client).skipped == "locked"
    assert client.windows == []


def test_lock_released_after_run(catalog: Engine) -> None:
    sync(catalog, history())
    with catalog.connect() as conn:
        assert conn.execute(select(func.pg_try_advisory_lock(LOCK_KEY))).scalar_one()
        conn.execute(select(func.pg_advisory_unlock(LOCK_KEY)))


def test_budget_exhausted_then_resume(catalog: Engine) -> None:
    client = history()
    ticks = iter([0.0, 0.0, 100.0, 100.0])  # deadline set, first fetch ok, then out of time
    result = sync(catalog, client, since=date(2026, 9, 1), budget_s=60, clock=lambda: next(ticks))
    assert result.partial
    assert client.set_calls == [3]
    assert result.strength_sessions == 1 and result.activities == 2  # old strength skipped
    assert count(catalog, sync_state) == 0  # cursor not moved

    result = sync(catalog, client, since=date(2026, 9, 1))
    assert not result.partial
    assert count(catalog, activities) == 3


def test_unmapped_exercise(db: Engine) -> None:
    client = FakeClient([strength(1, "2026-10-03 22:00:00")])  # empty exercise catalog
    result = sync(db, client)
    assert result.unmapped_sets == 2
    assert count(db, exercise_session_stats) == 0
    with db.connect() as conn:
        mapped = conn.execute(select(performed_sets.c.exercise_id)).scalars().all()
    assert mapped == [None, None]


def test_alias_maps_unknown_pair_on_resync(db: Engine) -> None:
    with db.begin() as conn:
        seed_exercises(conn, [CatalogEntry("Bench (alias target)", "BENCH_PRESS", "OTHER")])
        target = conn.execute(select(exercises.c.id)).scalar_one()
        conn.execute(
            exercise_aliases.insert().values(
                garmin_category=BENCH[0], garmin_name=BENCH[1], exercise_id=target
            )
        )
    result = sync(db, FakeClient([strength(1, "2026-10-03 22:00:00")]))
    assert result.unmapped_sets == 0
    with db.connect() as conn:
        mapped = conn.execute(select(performed_sets.c.exercise_id)).scalars().all()
    assert mapped == [target, target]
    assert count(db, exercise_session_stats) == 1


def test_catalog_match_wins_over_alias(catalog: Engine) -> None:
    with catalog.begin() as conn:
        bench = conn.execute(select(exercises.c.id)).scalar_one()
        seed_exercises(conn, [CatalogEntry("Other", "BENCH_PRESS", "OTHER")])
        other = conn.execute(
            select(exercises.c.id).where(exercises.c.garmin_name == "OTHER")
        ).scalar_one()
        conn.execute(
            exercise_aliases.insert().values(
                garmin_category=BENCH[0], garmin_name=BENCH[1], exercise_id=other
            )
        )
    sync(catalog, FakeClient([strength(1, "2026-10-03 22:00:00")]))
    with catalog.connect() as conn:
        mapped = set(conn.execute(select(performed_sets.c.exercise_id)).scalars())
    assert mapped == {bench}


def test_error_mid_run_keeps_committed_work(catalog: Engine) -> None:
    client = history()
    client.fail_on = 1
    with pytest.raises(GarminRateLimited):
        sync(catalog, client, since=date(2026, 9, 1))
    with catalog.connect() as conn:
        ids = conn.execute(select(activities.c.garmin_activity_id)).scalars().all()
    assert sorted(ids) == [2, 3]  # run and newest strength kept, failed one absent
    assert count(catalog, sync_state) == 0
    with catalog.connect() as conn:
        assert conn.execute(select(func.pg_try_advisory_lock(LOCK_KEY))).scalar_one()
        conn.execute(select(func.pg_advisory_unlock(LOCK_KEY)))


def test_uses_athlete_time_zone(catalog: Engine) -> None:
    with catalog.begin() as conn:
        conn.execute(text("INSERT INTO athlete (id, time_zone) VALUES (1, 'UTC')"))
    client = FakeClient([strength(1, "2026-10-04 02:00:00")])
    sync(catalog, client)
    with catalog.connect() as conn:
        assert conn.execute(select(activities.c.local_date)).scalar_one() == date(2026, 10, 4)


def test_rebuild_after_curation_adds_e1rm(catalog: Engine) -> None:
    sync(catalog, history(), since=date(2026, 9, 1))
    with catalog.begin() as conn:
        bench_id = conn.execute(
            update(exercises).values(e1rm_eligible=True).returning(exercises.c.id)
        ).scalar_one()
        assert stats.rebuild_all(conn, exercise_id=bench_id) == 2
    with catalog.connect() as conn:
        e1rms = conn.execute(select(exercise_session_stats.c.best_e1rm_kg)).scalars().all()
    assert e1rms == [Decimal("100.552")] * 2  # 86.187 x (1 + 5/30)


def test_rebuild_drops_stale_rows(catalog: Engine) -> None:
    sync(catalog, history(), since=date(2026, 9, 1))
    with catalog.begin() as conn:
        conn.execute(update(performed_sets).values(exercise_id=None))
        assert stats.rebuild_all(conn) == 2
    assert count(catalog, exercise_session_stats) == 0


# Linking scheduled sessions (Phase 4, decisions.md) ------------------------------


def _schedule(db: Engine, rows: list[tuple[int, int, date, str]]) -> dict[tuple[int, int], int]:
    """Plan with days 1 and 2; rows are (day_order, week_no, date, status)."""
    with db.begin() as conn:
        plan_id = conn.execute(
            insert(plans)
            .values(
                name="Block",
                status="active",
                weeks=4,
                sessions_per_week=2,
                progression_model="linear",
                end_criteria={},
                rationale="test",
            )
            .returning(plans.c.id)
        ).scalar_one()
        days = {
            order: conn.execute(
                insert(plan_days)
                .values(plan_id=plan_id, label=f"Day {order}", day_order=order)
                .returning(plan_days.c.id)
            ).scalar_one()
            for order in (1, 2)
        }
        return {
            (order, week): conn.execute(
                insert(scheduled_sessions)
                .values(plan_day_id=days[order], week_no=week, scheduled_date=day, status=status)
                .returning(scheduled_sessions.c.id)
            ).scalar_one()
            for order, week, day, status in rows
        }


def _links(db: Engine) -> dict[int, tuple[int | None, str]]:
    with db.connect() as conn:
        return {
            r.id: (r.activity_id, r.status)
            for r in conn.execute(select(scheduled_sessions).order_by(scheduled_sessions.c.id))
        }


def test_sync_links_sessions_by_date(catalog: Engine) -> None:
    ids = _schedule(
        catalog,
        [
            (2, 1, date(2026, 9, 20), "planned"),  # same date as day 1: day order wins
            (1, 1, date(2026, 9, 20), "planned"),
            (1, 2, date(2026, 10, 1), "planned"),  # no activity that day
            (1, 3, date(2026, 10, 3), "skipped"),  # not open
            (2, 3, date(2026, 10, 3), "pushed"),
        ],
    )
    client = history()
    client.summaries.append(run(4, "2026-10-03 12:00:00"))  # a run never links
    result = sync(catalog, client, since=date(2026, 9, 1))
    assert result.linked_sessions == 2
    assert _links(catalog) == {
        ids[(2, 1)]: (None, "planned"),
        ids[(1, 1)]: (1, "completed"),
        ids[(1, 2)]: (None, "planned"),
        ids[(1, 3)]: (None, "skipped"),
        ids[(2, 3)]: (3, "completed"),
    }
    # Idempotent: a second run links nothing new and moves nothing.
    assert sync(catalog, client, force=True).linked_sessions == 0
    assert _links(catalog)[ids[(1, 1)]] == (1, "completed")


def test_link_sessions_without_open_sessions(catalog: Engine) -> None:
    sync(catalog, history(), since=date(2026, 9, 1))
    with catalog.begin() as conn:
        assert link_sessions(conn, date(2026, 9, 1), date(2026, 10, 4)) == 0


def test_link_sessions_respects_the_window(catalog: Engine) -> None:
    sync(catalog, history(), since=date(2026, 9, 1))
    ids = _schedule(catalog, [(1, 1, date(2026, 9, 20), "planned")])
    with catalog.begin() as conn:
        assert link_sessions(conn, date(2026, 10, 1), date(2026, 10, 4)) == 0
        assert link_sessions(conn, date(2026, 9, 1), date(2026, 10, 4)) == 1
    assert _links(catalog)[ids[(1, 1)]] == (1, "completed")


# Linking by pushed workout id and judging against the prescription (Phase 5) -------


def _push(db: Engine, session_id: int, workout_id: int, sets: list[tuple[int, int]]) -> int:
    """Mark a session pushed with a workout id and prescribe bench sets (lb, reps)."""
    with db.begin() as conn:
        bench = conn.execute(
            select(exercises.c.id).where(exercises.c.garmin_name == BENCH[1])
        ).scalar_one()
        conn.execute(
            update(scheduled_sessions)
            .where(scheduled_sessions.c.id == session_id)
            .values(status="pushed", garmin_workout_id=workout_id)
        )
        conn.execute(
            insert(prescribed_sets),
            [
                {
                    "scheduled_session_id": session_id,
                    "exercise_id": bench,
                    "set_no": n,
                    "target_reps": reps,
                    "target_weight_kg": lb_to_kg(lb, 5),
                }
                for n, (lb, reps) in enumerate(sets, start=1)
            ],
        )
        return bench


def _from_workout(activity_id: int, start_gmt: str, workout_id: int) -> dict[str, Any]:
    return {**strength(activity_id, start_gmt), "workoutId": workout_id}


def _met(db: Engine, activity_id: int) -> list[tuple[int | None, bool | None]]:
    with db.connect() as conn:
        return [
            (r.scheduled_session_id, r.met_prescription)
            for r in conn.execute(
                select(exercise_session_stats).where(
                    exercise_session_stats.c.activity_id == activity_id
                )
            )
        ]


def test_sync_links_by_workout_id_before_date(catalog: Engine) -> None:
    ids = _schedule(
        catalog,
        [
            (1, 1, date(2026, 9, 19), "planned"),  # pushed for the day before
            (2, 1, date(2026, 9, 20), "planned"),  # same date as the activity
        ],
    )
    _push(catalog, ids[(1, 1)], 501, [(190, 5)])
    client = FakeClient([_from_workout(1, "2026-09-20 22:00:00", 501)])
    result = sync(catalog, client, since=date(2026, 9, 1))
    assert result.linked_sessions == 1
    assert _links(catalog) == {ids[(1, 1)]: (1, "completed"), ids[(2, 1)]: (None, "planned")}
    with catalog.connect() as conn:
        assert conn.execute(select(activities.c.garmin_workout_id)).scalar_one() == 501
    assert _met(catalog, 1) == [(ids[(1, 1)], True)]


def test_unknown_workout_id_falls_back_to_date(catalog: Engine) -> None:
    ids = _schedule(catalog, [(1, 1, date(2026, 9, 20), "planned")])
    client = FakeClient([_from_workout(1, "2026-09-20 22:00:00", 999)])
    assert sync(catalog, client, since=date(2026, 9, 1)).linked_sessions == 1
    assert _links(catalog) == {ids[(1, 1)]: (1, "completed")}
    assert _met(catalog, 1) == [(ids[(1, 1)], None)]  # nothing was prescribed


def test_one_activity_links_one_session(catalog: Engine) -> None:
    ids = _schedule(
        catalog,
        [(1, 1, date(2026, 9, 20), "planned"), (2, 1, date(2026, 9, 20), "planned")],
    )
    _push(catalog, ids[(2, 1)], 501, [(190, 5)])
    client = FakeClient([_from_workout(1, "2026-09-20 22:00:00", 501)])
    assert sync(catalog, client, since=date(2026, 9, 1)).linked_sessions == 1
    assert _links(catalog) == {ids[(1, 1)]: (None, "planned"), ids[(2, 1)]: (1, "completed")}


def test_missed_prescription(catalog: Engine) -> None:
    ids = _schedule(catalog, [(1, 1, date(2026, 9, 20), "planned")])
    _push(catalog, ids[(1, 1)], 501, [(190, 5), (190, 5)])  # the fixture has one real set
    sync(
        catalog, FakeClient([_from_workout(1, "2026-09-20 22:00:00", 501)]), since=date(2026, 9, 1)
    )
    assert _met(catalog, 1) == [(ids[(1, 1)], False)]


def test_rebuild_keeps_the_link(catalog: Engine) -> None:
    ids = _schedule(catalog, [(1, 1, date(2026, 9, 20), "planned")])
    _push(catalog, ids[(1, 1)], 501, [(190, 5)])
    sync(
        catalog, FakeClient([_from_workout(1, "2026-09-20 22:00:00", 501)]), since=date(2026, 9, 1)
    )
    with catalog.begin() as conn:
        stats.rebuild_all(conn)
    assert _met(catalog, 1) == [(ids[(1, 1)], True)]


@pytest.mark.parametrize(
    ("prescribed", "targets"),
    [
        pytest.param([(190, 5)], (5,), id="straight sets"),
        pytest.param([(190, 3), (170, 6), (170, 6)], (3,), id="back-offs left out"),
    ],
)
def test_exposures_carry_stored_targets(
    catalog: Engine, prescribed: list[tuple[int, int]], targets: tuple[int, ...]
) -> None:
    ids = _schedule(catalog, [(1, 1, date(2026, 9, 20), "planned")])
    bench = _push(catalog, ids[(1, 1)], 501, prescribed)
    client = FakeClient(
        [
            _from_workout(1, "2026-09-20 22:00:00", 501),
            strength(3, "2026-10-03 22:00:00"),  # not pushed: no stored targets
        ]
    )
    sync(catalog, client, since=date(2026, 9, 1))
    with catalog.connect() as conn:
        found = exposures(conn, bench, date(2026, 10, 5))
    assert [e.targets for e in found] == [targets, None]
