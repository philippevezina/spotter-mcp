"""Timed exercises in process: curation, stats, plans, proposals, push and sync.

A side plank is curated as timed (measure time). Garmin logs a hold with no reps and the
seconds in `duration`; the plan stores a hold range; the watch counts each hold down.
"""

# The planning and push fixtures are imported, so test arguments shadow them by design.
# ruff: noqa: F811

from __future__ import annotations

import copy
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, insert, select

from spotter.db.schema import activities, exercises, performed_sets
from spotter.db.seed import seed_exercises
from spotter.garmin.client import CatalogEntry
from spotter.sync.stats import rebuild_activity_stats
from spotter.units import lb_to_kg
from tests.mcp.conftest import Call
from tests.mcp.test_planning import (  # noqa: F401  (fixtures)
    CATALOG,
    CHECK_DAY,
    START,
    Clock,
    by_name,
    clock,
    curate_all,
    ex,
    mcp,
    session_id,
)
from tests.mcp.test_push import FakeWriter, _prescribed, garmin, writer  # noqa: F401
from tests.test_sync import _load, strength

PLANK = CatalogEntry("Side Plank", "PLANK", "SIDE_PLANK")
PLANK_CURATION = {
    "movement_pattern": "core",
    "primary_muscles": ["core"],
    "load_type": "bodyweight",
    "measure": "time",
}


@pytest.fixture
def ids(db: Engine) -> dict[str, int]:
    with db.begin() as conn:
        seed_exercises(conn, [*CATALOG.values(), PLANK])
        by_name = {
            r.garmin_name: r.id
            for r in conn.execute(select(exercises.c.id, exercises.c.garmin_name))
        }
    return {**{k: by_name[e.name] for k, e in CATALOG.items()}, "plank": by_name[PLANK.name]}


def add_holds(
    db: Engine,
    plank_id: int,
    activity_id: int,
    day: date,
    holds: list[str],
    weight_lb: float | None = None,
) -> None:
    """A strength activity on `day` with timed sets as Garmin logs them: no reps."""
    with db.begin() as conn:
        conn.execute(
            insert(activities).values(
                garmin_activity_id=activity_id,
                type="strength_training",
                start_time=datetime.combine(day, time(22, 0), tzinfo=UTC),
                local_date=day,
                raw_json={},
            )
        )
        conn.execute(
            insert(performed_sets),
            [
                {
                    "activity_id": activity_id,
                    "set_index": i,
                    "exercise_id": plank_id,
                    "garmin_category": PLANK.category,
                    "garmin_name": PLANK.name,
                    "reps": None,
                    "duration_s": Decimal(h),
                    "weight_kg": None if weight_lb is None else lb_to_kg(weight_lb, Decimal(5)),
                }
                for i, h in enumerate(holds)
            ],
        )
        rebuild_activity_stats(conn, activity_id)


def core_plan(ids: dict[str, int], **plank: Any) -> dict[str, Any]:
    entry = {
        "exercise_id": ids["plank"],
        "role": "accessory",
        "sets": 2,
        "hold_min_s": 30,
        "hold_max_s": 45,
        "target_rir": 2,
        "rest_s": 60,
        **plank,
    }
    return {
        "name": "Core block",
        "rationale": "Side plank holds, double progression on time.",
        "weeks": 6,
        "progression_model": "linear",
        "days": [{"label": "Core", "preferred_weekday": "tue", "exercises": [entry]}],
    }


def activate_core(call: Call, ids: dict[str, int], clock: Clock) -> dict[str, Any]:
    call("curate_exercise", exercise_id=ids["plank"], **PLANK_CURATION)
    clock.now = CHECK_DAY
    plan_id = call("create_plan", plan=core_plan(ids))["plan_id"]
    return call("activate_plan", plan_id=plan_id, start_date=START.isoformat())


# Curation and stats -----------------------------------------------------------------


def test_curate_timed(call: Call, ids: dict[str, int]) -> None:
    out = call(
        "curate_exercise", exercise_id=ids["plank"], **{**PLANK_CURATION, "e1rm_eligible": True}
    )
    assert (out["measure"], out["e1rm_eligible"]) == ("time", False)
    [found] = call("search_exercises", query="side plank")["exercises"]
    assert found["measure"] == "time"
    # Re-curating without measure keeps it.
    kept = {k: v for k, v in PLANK_CURATION.items() if k != "measure"}
    assert call("curate_exercise", exercise_id=ids["plank"], **kept)["measure"] == "time"


def test_curating_as_timed_rebuilds_stats(call: Call, db: Engine, ids: dict[str, int]) -> None:
    add_holds(db, ids["plank"], 300, date(2026, 9, 8), ["30.0", "30.0"])
    assert call("get_exercise_history", exercise_id=ids["plank"])["sessions"] == []  # no reps
    out = call("curate_exercise", exercise_id=ids["plank"], **PLANK_CURATION)
    assert out["activities_rebuilt"] == 1
    history = call("get_exercise_history", exercise_id=ids["plank"])
    assert history["exercise"]["measure"] == "time"
    [session] = history["sessions"]
    assert session["top_set"] == {"weight_lb": None, "hold_s": 30.0}
    assert (session["total_hold_s"], session["working_sets"], session["volume_lb"]) == (
        60.0,
        2,
        None,
    )


def test_hold_trend_and_loaded_holds(call: Call, db: Engine, ids: dict[str, int]) -> None:
    call("curate_exercise", exercise_id=ids["plank"], **PLANK_CURATION)
    add_holds(db, ids["plank"], 300, date(2026, 9, 8), ["30.0", "29.6"])
    add_holds(db, ids["plank"], 301, date(2026, 9, 15), ["45.0", "40.0"])
    trend = call("get_exercise_history", exercise_id=ids["plank"])["trend"]
    assert (trend["metric"], trend["first"], trend["last"], trend["direction"]) == (
        "top_set_hold",
        30.0,
        45.0,
        "up",
    )
    add_holds(db, ids["plank"], 302, date(2026, 9, 22), ["20.0", "35.0"], weight_lb=25)
    newest = call("get_exercise_history", exercise_id=ids["plank"])["sessions"][0]
    assert newest["top_set"] == {"weight_lb": 25.0, "hold_s": 35.0}


# Plans ----------------------------------------------------------------------------


def test_create_plan_checks_ranges_against_measure(
    call: Call, ids: dict[str, int], clock: Clock
) -> None:
    call("curate_exercise", exercise_id=ids["plank"], **PLANK_CURATION)
    curate_all(call, ids, "bench")
    clock.now = CHECK_DAY
    with pytest.raises(ToolError, match="Side Plank \\(timed\\) takes hold_min_s and hold_max_s"):
        call(
            "create_plan",
            plan=core_plan(ids, hold_min_s=None, hold_max_s=None, rep_min=8, rep_max=12),
        )
    with pytest.raises(ToolError, match="hold_min_s 45 > hold_max_s 30"):
        call("create_plan", plan=core_plan(ids, hold_min_s=45, hold_max_s=30))
    bench = ex(ids, "bench", "main", 3, 5, 5)
    plan = core_plan(ids)
    plan["days"][0]["exercises"].append({**bench, "hold_min_s": 30, "hold_max_s": 40})
    with pytest.raises(ToolError, match="Barbell Bench Press \\(reps\\) takes rep_min and rep_max"):
        call("create_plan", plan=plan)


def test_timed_override_model_warns(call: Call, ids: dict[str, int], clock: Clock) -> None:
    call("curate_exercise", exercise_id=ids["plank"], **PLANK_CURATION)
    clock.now = CHECK_DAY
    out = call("create_plan", plan=core_plan(ids, progression_override={"model": "linear"}))
    assert "timed_double_progression:Side Plank" in out["warnings"]


def test_get_plan_shows_hold_range(call: Call, ids: dict[str, int], clock: Clock) -> None:
    activate_core(call, ids, clock)
    [day] = call("get_plan")["days"]
    [plank] = day["exercises"]
    assert (plank["hold_min_s"], plank["hold_max_s"]) == (30, 45)
    assert "rep_min" not in plank


def test_measure_cannot_change_in_an_open_plan(
    call: Call, ids: dict[str, int], clock: Clock
) -> None:
    activate_core(call, ids, clock)
    with pytest.raises(ToolError, match="in a draft or active plan with time ranges"):
        call("curate_exercise", exercise_id=ids["plank"], **{**PLANK_CURATION, "measure": "reps"})


# Proposals --------------------------------------------------------------------------


def test_propose_adds_five_seconds(
    call: Call, db: Engine, ids: dict[str, int], clock: Clock
) -> None:
    add_holds(db, ids["plank"], 300, START + timedelta(days=1), ["40.0", "40.4"])
    plan = activate_core(call, ids, clock)
    plank = by_name(call("propose_next_session", scheduled_session_id=session_id(plan, "Core", 4)))[
        "Side Plank"
    ]
    assert (plank["model"], plank["target_hold_s"], plank["rule_fired"]) == (
        "double_progression",
        [45, 45],
        "double_progression:+5s",
    )
    assert "target_reps" not in plank
    assert plank["last"] == {"weight_lb": None, "hold_s": [40, 40]}
    lift = call("evaluate_plan")["lifts"][0]
    assert lift["last"] == {"date": "2026-09-15", "weight_lb": None, "hold_s": [40, 40]}


def test_new_hold_range_needs_calibration(
    call: Call, db: Engine, ids: dict[str, int], clock: Clock
) -> None:
    add_holds(db, ids["plank"], 300, START - timedelta(days=7), ["20.0", "20.0"])
    plan = activate_core(call, ids, clock)
    plank = by_name(call("propose_next_session", scheduled_session_id=session_id(plan, "Core", 4)))[
        "Side Plank"
    ]
    assert (plank["status"], plank["reasons"]) == ("needs_calibration", ["new_hold_range"])


# Push and sync ----------------------------------------------------------------------


def test_push_counts_holds_down_and_sync_judges_them(
    call: Call, db: Engine, ids: dict[str, int], clock: Clock, writer: FakeWriter
) -> None:
    add_holds(db, ids["plank"], 300, START + timedelta(days=1), ["30.0", "25.0"])  # a miss
    plan = activate_core(call, ids, clock)
    sid = session_id(plan, "Core", 4)
    dry = call("push_session", scheduled_session_id=sid, dry_run=True)
    assert dry["exercises"][0]["sets"] == [{"hold_s": 30, "weight_lb": None}] * 2
    assert writer.workouts == {}

    pushed = call("push_session", scheduled_session_id=sid)
    [group] = writer.workouts[pushed["garmin_workout_id"]]["workoutSegments"][0]["workoutSteps"]
    step = group["workoutSteps"][0]
    assert (step["endCondition"]["conditionTypeKey"], step["endConditionValue"]) == ("time", 30.0)
    assert [(r.target_reps, r.target_hold_s) for r in _prescribed(db, sid)] == [(None, 30)] * 2

    # Trained from the watch: the timed fixture logs two 30 s side planks.
    clock.now = datetime(2026, 10, 7, 16, 0, tzinfo=UTC)
    summary = strength(500, "2026-10-06 22:00:00")
    summary["workoutId"] = pushed["garmin_workout_id"]
    writer.summaries.append(summary)
    writer.sets[500] = copy.deepcopy(_load("exercise_sets_timed.json"))
    snap = call("get_training_snapshot", force_sync=True)
    planned = snap["recent_strength"][0]["planned"]
    [plank] = planned["exercises"]
    assert plank["prescribed"] == [{"hold_s": 30, "weight_lb": None}] * 2
    assert plank["met_prescription"] is True
    session = call("get_exercise_history", exercise_id=ids["plank"])["sessions"][0]
    assert (session["date"], session["top_set"]["hold_s"]) == ("2026-10-06", 30.0)


def test_push_rejects_reps_for_a_timed_exercise(
    call: Call, ids: dict[str, int], clock: Clock
) -> None:
    plan = activate_core(call, ids, clock)
    sets = [{"reps": 10}]
    with pytest.raises(ToolError, match="Side Plank takes hold_s \\(it is timed\\)"):
        call(
            "push_session",
            scheduled_session_id=session_id(plan, "Core", 4),
            exercises=[{"exercise_id": ids["plank"], "sets": sets}],
            dry_run=True,
        )


def test_push_set_needs_reps_or_hold(call: Call, ids: dict[str, int], clock: Clock) -> None:
    plan = activate_core(call, ids, clock)
    with pytest.raises(ToolError, match="reps or hold_s"):
        call(
            "push_session",
            scheduled_session_id=session_id(plan, "Core", 4),
            exercises=[{"exercise_id": ids["plank"], "sets": [{"reps": 5, "hold_s": 30}]}],
            dry_run=True,
        )
