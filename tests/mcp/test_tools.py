"""MCP tools in process: input validation and output shape. No network."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, func, insert, select, update

from spotter.db.schema import (
    daily_metrics,
    exercise_aliases,
    exercise_session_stats,
    exercises,
    performed_sets,
    plan_days,
    plan_exercises,
    plans,
    scheduled_sessions,
)
from spotter.db.seed import seed_exercises
from spotter.garmin.client import CatalogEntry
from spotter.garmin.errors import GarminAuthExpired, GarminRateLimited, GarminUnavailable
from spotter.sync.sync import LOCK_KEY
from tests.mcp.conftest import Call, FakeGarmin
from tests.test_sync import BENCH, FakeClient, run, strength


def bench_id(db: Engine) -> int:
    with db.connect() as conn:
        return conn.execute(
            select(exercises.c.id).where(exercises.c.garmin_name == BENCH[1])
        ).scalar_one()


# Snapshot -------------------------------------------------------------------


def test_snapshot_syncs_and_summarizes(call: Call, garmin: FakeGarmin) -> None:
    snap = call("get_training_snapshot")
    assert garmin.opens == 1
    assert snap["sync"]["activities"] == 3
    assert snap["sync"]["tokens_refreshed"] is False
    assert snap["sync"]["last_synced_at"] == "2026-10-04T16:00:00+00:00"
    assert snap["today"] == "2026-10-04"
    assert snap["athlete"] is None
    assert snap["plan"] is None

    recent = snap["recent_strength"]
    assert [s["date"] for s in recent] == ["2026-10-03", "2026-09-20"]
    bench = recent[0]["exercises"][0]
    assert bench["name"] == "Barbell Bench Press"
    assert bench["top_set"] == {"weight_lb": 190.0, "reps": 5}
    assert bench["working_sets"] == 1
    assert recent[0]["planned"] is None

    rollup = snap["strength_28d"]
    assert rollup["start"] == "2026-09-07"
    assert rollup["sessions"] == 2
    assert rollup["session_dates"] == ["2026-09-20", "2026-10-03"]
    [ex] = rollup["exercises"]
    assert ex["exposures"] == 2
    assert ex["working_sets"] == 2
    assert ex["volume_lb"] == 1900.0  # 2 x 190 lb x 5
    assert ex["trend"]["metric"] == "top_set_weight"
    assert ex["trend"]["direction"] == "flat"

    assert snap["readiness"]["today"]["class"] == "no_data"
    assert snap["readiness"]["last_28_days"]["no_data"] == 28
    assert snap["endurance"]["last_7_days"]["run"]["sessions"] == 0
    assert snap["flags"]["unmapped_exercises"] == []
    assert snap["flags"]["missed_sessions"] == []
    assert snap["flags"]["stalls"] == []
    assert snap["flags"]["stalls_note"] == "No active plan."


def test_snapshot_skips_recent_sync_without_opening_garmin(call: Call, garmin: FakeGarmin) -> None:
    call("get_training_snapshot")
    snap = call("get_training_snapshot")
    assert snap["sync"]["skipped"] == "recent"
    assert garmin.opens == 1
    assert call("get_training_snapshot", force_sync=True)["sync"]["skipped"] is None
    assert garmin.opens == 2


def test_snapshot_reports_garmin_error_and_uses_stored_data(call: Call, garmin: FakeGarmin) -> None:
    garmin.error = GarminAuthExpired()
    snap = call("get_training_snapshot")
    assert "spotter bootstrap-login" in snap["sync"]["error"]
    assert snap["recent_strength"] == []


def test_snapshot_reports_lock(call: Call, catalog: Engine) -> None:
    with catalog.connect() as other:
        other.execute(select(func.pg_advisory_lock(LOCK_KEY)))
        snap = call("get_training_snapshot")
        other.execute(select(func.pg_advisory_unlock(LOCK_KEY)))
    assert snap["sync"]["skipped"] == "locked"


def test_snapshot_flags_unmapped(call: Call, garmin: FakeGarmin, catalog: Engine) -> None:
    with catalog.begin() as conn:
        conn.execute(exercises.delete())
    snap = call("get_training_snapshot")
    assert snap["flags"]["unmapped_exercises"] == [
        {"garmin_category": BENCH[0], "garmin_name": BENCH[1], "sets": 4, "last_date": "2026-10-03"}
    ]
    assert snap["recent_strength"][0]["unmapped_sets"] == 2


# Plan, with an interference flag on a lower-body day ----------------------------


@pytest.fixture
def plan(catalog: Engine) -> Iterator[int]:
    with catalog.begin() as conn:
        seed_exercises(conn, [CatalogEntry("Barbell Back Squat", "SQUAT", "BARBELL_BACK_SQUAT")])
        squat = conn.execute(
            update(exercises)
            .where(exercises.c.garmin_category == "SQUAT")
            .values(curated=True, movement_pattern="squat")
            .returning(exercises.c.id)
        ).scalar_one()
        plan_id = conn.execute(
            insert(plans)
            .values(
                name="Block 1",
                status="active",
                start_date=date(2026, 9, 21),
                weeks=6,
                sessions_per_week=3,
                progression_model="linear",
                end_criteria={"max_weeks": 6},
                rationale="test",
            )
            .returning(plans.c.id)
        ).scalar_one()
        day = conn.execute(
            insert(plan_days)
            .values(plan_id=plan_id, label="Lower A", day_order=1)
            .returning(plan_days.c.id)
        ).scalar_one()
        conn.execute(
            insert(plan_exercises).values(
                plan_day_id=day,
                exercise_id=squat,
                position=1,
                role="main",
                sets=3,
                rep_min=5,
                rep_max=5,
                target_rir=2,
                rest_s=180,
            )
        )
        conn.execute(
            insert(scheduled_sessions),
            [
                {"plan_day_id": day, "week_no": 2, "scheduled_date": date(2026, 10, 1)},
                {"plan_day_id": day, "week_no": 3, "scheduled_date": date(2026, 10, 5)},
            ],
        )
    yield plan_id


def test_get_plan_none(call: Call) -> None:
    assert call("get_plan")["plan"] is None
    with pytest.raises(ToolError, match="No plan with id 99"):
        call("get_plan", plan_id=99)


def test_get_plan_active(call: Call, plan: int) -> None:
    out = call("get_plan")
    assert out["plan"]["id"] == plan
    assert out["plan"]["week"] == 2  # 2026-10-04 is day 13 of the block
    assert out["plan"]["next_session"]["date"] == "2026-10-05"
    assert out["days"][0]["exercises"][0]["name"] == "Barbell Back Squat"
    assert [s["week_no"] for s in out["schedule"]] == [2, 3]
    assert call("get_plan", plan_id=plan)["plan"]["id"] == plan


def test_snapshot_plan_flags(call: Call, plan: int, garmin: FakeGarmin) -> None:
    long_run = {**run(9, "2026-10-04 11:00:00"), "duration": 90 * 60.0}
    garmin.client = FakeClient([long_run])
    snap = call("get_training_snapshot")
    assert snap["plan"]["next_session"]["label"] == "Lower A"
    assert [f["activity_id"] for f in snap["endurance"]["interference_flags"]] == [9]
    assert snap["endurance"]["hard_last_48h"][0]["hard_reasons"] == ["run_duration_s:5400>=4500"]
    assert snap["endurance"]["last_7_days"]["run"]["distance_km"] == 10.0
    assert [m["date"] for m in snap["flags"]["missed_sessions"]] == ["2026-10-01"]


# Exercise history -------------------------------------------------------------


def test_exercise_history(call: Call, catalog: Engine) -> None:
    call("get_training_snapshot")
    out = call("get_exercise_history", exercise_id=bench_id(catalog))
    assert out["exercise"]["name"] == "Barbell Bench Press"
    assert [s["date"] for s in out["sessions"]] == ["2026-10-03", "2026-09-20"]
    assert out["sessions"][0]["top_set"] == {"weight_lb": 190.0, "reps": 5}
    assert out["sessions"][0]["e1rm_lb"] is None
    assert out["trend"]["points"] == 2
    since = call("get_exercise_history", exercise_id=bench_id(catalog), since="2026-10-01")
    assert len(since["sessions"]) == 1


def test_exercise_history_validation(call: Call) -> None:
    with pytest.raises(ToolError, match="No exercise with id 999"):
        call("get_exercise_history", exercise_id=999)
    with pytest.raises(ToolError):
        call("get_exercise_history", exercise_id=1, limit=0)


# Readiness and endurance --------------------------------------------------------


def metrics_row(day: date, **values: Any) -> dict[str, Any]:
    return {"local_date": day, "raw_json": {}, **values}


def test_readiness(call: Call, catalog: Engine, garmin: FakeGarmin) -> None:
    with catalog.begin() as conn:
        for row in [
            metrics_row(date(2026, 10, 2), resting_hr=60, hrv_overnight_ms=30, sleep_score=80),
            metrics_row(
                date(2026, 10, 3),
                resting_hr=64,
                hrv_overnight_ms=40,
                hrv_baseline_low=35,
                hrv_baseline_high=50,
                sleep_score=55,
            ),
            metrics_row(date(2026, 10, 4), training_readiness=20, bodyweight_kg=81.65),
        ]:
            conn.execute(insert(daily_metrics).values(**row))
    out = call("get_readiness", days=4)
    assert [d["date"] for d in out["days"]] == [
        "2026-10-04",
        "2026-10-03",
        "2026-10-02",
        "2026-10-01",
    ]
    assert [d["class"] for d in out["days"]] == ["red", "amber", "green", "no_data"]
    assert out["days"][0]["reasons"][0] == "training_readiness:20<25"
    assert out["summary"] == {"days": 4, "green": 1, "amber": 1, "red": 1, "no_data": 1}
    assert out["baselines"]["hrv_baseline_low_ms"] == 35
    assert out["baselines"]["resting_hr_avg"] == 62.0
    assert out["baselines"]["bodyweight_lb"] == 180.0

    garmin.error = GarminUnavailable()  # the fake has no metrics; keep the seeded rows
    snap = call("get_training_snapshot")
    assert snap["readiness"]["today"]["class"] == "red"
    assert snap["readiness"]["last_7_days"]["red"] == 1


def test_readiness_validation(call: Call) -> None:
    with pytest.raises(ToolError):
        call("get_readiness", days=0)
    with pytest.raises(ToolError):
        call("get_readiness", days=91)


def test_endurance_load(call: Call) -> None:
    call("get_training_snapshot")
    out = call("get_endurance_load", days=28)
    [act] = out["activities"]
    assert act["type"] == "running"
    assert (act["duration_min"], act["distance_km"]) == (60.0, 10.0)
    assert act["hard_reasons"] == []
    [week] = out["weekly_totals"]
    assert week["week_start"] == "2026-09-21"  # Monday
    assert week["run"]["sessions"] == 1
    assert week["ride"]["sessions"] == 0
    with pytest.raises(ToolError):
        call("get_endurance_load", days=181)


# Search -----------------------------------------------------------------------------


def test_search_exercises(call: Call, catalog: Engine) -> None:
    with catalog.begin() as conn:
        seed_exercises(
            conn,
            [
                CatalogEntry("Dumbbell Bench Press", "BENCH_PRESS", "DUMBBELL_BENCH_PRESS"),
                CatalogEntry("Barbell Back Squat", "SQUAT", "BARBELL_BACK_SQUAT"),
            ],
        )
    names = [e["name"] for e in call("search_exercises", query="bench press")["exercises"]]
    assert names == ["Barbell Bench Press", "Dumbbell Bench Press"]
    by_key = call("search_exercises", query="bench_press")["exercises"]
    assert len(by_key) == 2
    dumbbell = call("search_exercises", query="bench", equipment="dumbbell")["exercises"]
    assert [e["name"] for e in dumbbell] == ["Dumbbell Bench Press"]
    assert call("search_exercises", query="bench", pattern="push_h")["exercises"] == []
    assert call("search_exercises", query="50%")["exercises"] == []
    with pytest.raises(ToolError):
        call("search_exercises", query="")


# Admin ------------------------------------------------------------------------------


def test_map_exercise(call: Call, catalog: Engine, garmin: FakeGarmin) -> None:
    with catalog.begin() as conn:
        conn.execute(exercises.delete())
        seed_exercises(conn, [CatalogEntry("Bench (mine)", "BENCH_PRESS", "MINE")])
        target = conn.execute(select(exercises.c.id)).scalar_one()
    call("get_training_snapshot")
    out = call("map_exercise", garmin_category=BENCH[0], garmin_name=BENCH[1], exercise_id=target)
    assert out["sets_remapped"] == 4
    assert out["activities_rebuilt"] == 2
    with catalog.connect() as conn:
        assert conn.execute(select(func.count()).select_from(exercise_aliases)).scalar_one() == 1
        assert (
            conn.execute(select(func.count()).select_from(exercise_session_stats)).scalar_one() == 2
        )
        mapped = set(conn.execute(select(performed_sets.c.exercise_id)).scalars())
    assert mapped == {target}

    again = call("map_exercise", garmin_category=BENCH[0], garmin_name=BENCH[1], exercise_id=target)
    assert again["sets_remapped"] == 0  # idempotent

    # The mapping survives a forced re-sync of recent sets.
    call("sync_garmin")
    snap = call("get_training_snapshot")
    assert snap["flags"]["unmapped_exercises"] == []


def test_map_exercise_refusals(call: Call, catalog: Engine) -> None:
    with pytest.raises(ToolError, match="already in the catalog"):
        call("map_exercise", garmin_category=BENCH[0], garmin_name=BENCH[1], exercise_id=1)
    with pytest.raises(ToolError, match="No exercise with id 999"):
        call("map_exercise", garmin_category="X", garmin_name="Y", exercise_id=999)


def test_sync_garmin(call: Call, garmin: FakeGarmin) -> None:
    call("get_training_snapshot")
    out = call("sync_garmin", since="2026-09-01", full=True)
    assert out["skipped"] is None
    assert out["start"] == "2026-09-01"
    assert out["strength_sessions"] == 2
    with pytest.raises(ToolError, match="spotter backfill"):
        call("sync_garmin", since="2026-08-01")
    garmin.error = GarminRateLimited("Garmin returned 429.")
    with pytest.raises(ToolError, match="429"):
        call("sync_garmin")


def test_strength_activity_outside_window(call: Call, garmin: FakeGarmin) -> None:
    garmin.client = FakeClient([strength(5, "2026-09-01 22:00:00")])
    snap = call("get_training_snapshot", force_sync=True)
    assert snap["strength_28d"]["sessions"] == 0
    assert snap["recent_strength"][0]["date"] == "2026-09-01"
