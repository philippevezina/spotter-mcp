"""Cool-down stretches in process: plan validation, volume, proposals, push and sync.

A quad stretch ends the day with role cooldown: timed, a fixed hold, no target RIR, out
of volume and stalls, pushed as cool-down steps, logged without met_prescription.
"""

# The planning, push and timed fixtures are imported, so test arguments shadow them.
# ruff: noqa: F811

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, select

from spotter.db.schema import exercises
from spotter.db.seed import seed_exercises
from spotter.garmin.client import CatalogEntry
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
from tests.mcp.test_push import FakeWriter, garmin, writer  # noqa: F401
from tests.mcp.test_timed import PLANK, PLANK_CURATION, add_holds
from tests.test_sync import _load, strength

STRETCH = CatalogEntry("Quad Stretch", "WARM_UP", "STRETCH_QUAD")
STRETCH_CURATION = {
    "movement_pattern": "isolation",
    "primary_muscles": ["quads"],
    "load_type": "bodyweight",
    "measure": "time",
}


@pytest.fixture
def ids(db: Engine) -> dict[str, int]:
    with db.begin() as conn:
        seed_exercises(conn, [*CATALOG.values(), PLANK, STRETCH])
        by_name = {
            r.garmin_name: r.id
            for r in conn.execute(select(exercises.c.id, exercises.c.garmin_name))
        }
    return {
        **{k: by_name[e.name] for k, e in CATALOG.items()},
        "plank": by_name[PLANK.name],
        "stretch": by_name[STRETCH.name],
    }


def stretch(ids: dict[str, int], **kw: Any) -> dict[str, Any]:
    return {
        "exercise_id": ids["stretch"],
        "role": "cooldown",
        "sets": 2,
        "hold_min_s": 20,
        "hold_max_s": 30,
        "rest_s": 10,
        **kw,
    }


def plank(ids: dict[str, int]) -> dict[str, Any]:
    return {
        "exercise_id": ids["plank"],
        "role": "accessory",
        "sets": 2,
        "hold_min_s": 30,
        "hold_max_s": 45,
        "target_rir": 2,
        "rest_s": 60,
    }


def day_plan(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "name": "Core and stretch",
        "rationale": "Side planks, then a quad stretch to cool down.",
        "weeks": 6,
        "progression_model": "linear",
        "days": [{"label": "Core", "preferred_weekday": "tue", "exercises": entries}],
    }


def curate(call: Call, ids: dict[str, int]) -> None:
    call("curate_exercise", exercise_id=ids["plank"], **PLANK_CURATION)
    call("curate_exercise", exercise_id=ids["stretch"], **STRETCH_CURATION)


def activate(call: Call, ids: dict[str, int], clock: Clock) -> dict[str, Any]:
    curate(call, ids)
    clock.now = CHECK_DAY
    plan_id = call("create_plan", plan=day_plan([plank(ids), stretch(ids)]))["plan_id"]
    return call("activate_plan", plan_id=plan_id, start_date=START.isoformat())


# Plans ----------------------------------------------------------------------------


def test_cooldown_is_out_of_volume(call: Call, ids: dict[str, int], clock: Clock) -> None:
    curate(call, ids)
    clock.now = CHECK_DAY
    out = call("create_plan", plan=day_plan([plank(ids), stretch(ids)]))
    assert out["weekly_sets_per_muscle"] == {"core": 2.0}
    assert not any("quads" in w for w in out["warnings"])
    [day] = call("get_plan", plan_id=out["plan_id"])["days"]
    cooldown = day["exercises"][1]
    assert (cooldown["role"], cooldown["target_rir"]) == ("cooldown", None)


@pytest.mark.parametrize(
    ("entries", "error"),
    [
        (lambda i: [stretch(i), plank(i)], "Side Plank comes after the cool-down"),
        (lambda i: [plank(i), stretch(i, target_rir=1)], "Quad Stretch is a cool-down; drop"),
        (lambda i: [{**plank(i), "target_rir": None}], "Side Plank needs target_rir"),
        (
            lambda i: [ex(i, "bench", "cooldown", 2, 5, 5, rir=None)],
            "Barbell Bench Press is a cool-down but not timed",
        ),
    ],
    ids=["order", "rir", "missing_rir", "not_timed"],
)
def test_cooldown_validation(
    call: Call, ids: dict[str, int], clock: Clock, entries: Any, error: str
) -> None:
    curate(call, ids)
    curate_all(call, ids, "bench")
    clock.now = CHECK_DAY
    with pytest.raises(ToolError, match=error):
        call("create_plan", plan=day_plan(entries(ids)))


def test_weighted_cooldown_is_rejected(call: Call, ids: dict[str, int], clock: Clock) -> None:
    call(
        "curate_exercise", exercise_id=ids["stretch"], **{**STRETCH_CURATION, "load_type": "cable"}
    )
    clock.now = CHECK_DAY
    with pytest.raises(ToolError, match="Quad Stretch is a cool-down but needs a weight"):
        call("create_plan", plan=day_plan([stretch(ids)]))


# Proposals --------------------------------------------------------------------------


def test_cooldown_proposes_a_fixed_hold(
    call: Call, db: Engine, ids: dict[str, int], clock: Clock
) -> None:
    plan = activate(call, ids, clock)
    proposal = by_name(
        call("propose_next_session", scheduled_session_id=session_id(plan, "Core", 4))
    )
    quad = proposal["Quad Stretch"]
    assert (quad["status"], quad["target_hold_s"], quad["target_rir"], quad["rule_fired"]) == (
        "ok",
        [30, 30],
        None,
        "cooldown:fixed",
    )
    assert all(lift["role"] != "cooldown" for lift in call("evaluate_plan")["lifts"])


# Push and sync ----------------------------------------------------------------------


def test_push_ends_with_cooldown_steps_and_sync_logs_them(
    call: Call, db: Engine, ids: dict[str, int], clock: Clock, writer: FakeWriter
) -> None:
    add_holds(db, ids["plank"], 300, START + timedelta(days=1), ["30.0", "30.0"])
    plan = activate(call, ids, clock)
    sid = session_id(plan, "Core", 4)
    pushed = call("push_session", scheduled_session_id=sid)
    steps = writer.workouts[pushed["garmin_workout_id"]]["workoutSegments"][0]["workoutSteps"]
    tail = [s for s in steps if s.get("category") == "WARM_UP"]
    assert [(s["stepType"]["stepTypeKey"], s["endConditionValue"]) for s in tail] == [
        ("cooldown", 30.0),
        ("cooldown", 30.0),
    ]
    assert steps[-1]["category"] == "WARM_UP"

    # Trained from the watch: the timed fixture logs two side planks and one 15 s stretch.
    clock.now = datetime(2026, 10, 7, 16, 0, tzinfo=UTC)
    summary = strength(500, "2026-10-06 22:00:00")
    summary["workoutId"] = pushed["garmin_workout_id"]
    writer.summaries.append(summary)
    writer.sets[500] = copy.deepcopy(_load("exercise_sets_timed.json"))
    snap = call("get_training_snapshot", force_sync=True)
    done = {e["name"]: e for e in snap["recent_strength"][0]["planned"]["exercises"]}
    assert done["Side Plank"]["prescribed"] == [{"hold_s": 35, "weight_lb": None}] * 2
    assert done["Side Plank"]["met_prescription"] is False  # judged: 30 s held
    assert done["Quad Stretch"]["met_prescription"] is None  # short, but not judged
    session = call("get_exercise_history", exercise_id=ids["stretch"])["sessions"][0]
    assert session["top_set"]["hold_s"] == 15.0


def test_push_keeps_the_cooldown_last(call: Call, ids: dict[str, int], clock: Clock) -> None:
    plan = activate(call, ids, clock)
    with pytest.raises(ToolError, match="List the cool-down last; Side Plank comes after it"):
        call(
            "push_session",
            scheduled_session_id=session_id(plan, "Core", 4),
            exercises=[
                {"exercise_id": ids["stretch"], "sets": [{"hold_s": 30}]},
                {"exercise_id": ids["plank"], "sets": [{"hold_s": 30}]},
            ],
            dry_run=True,
        )
