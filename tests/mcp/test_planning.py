"""Planning tools in process: curation, plans, notes, proposals and evaluation.

The exit scenarios (decisions.md, "Phase 4 test sessions") run end to end through
curate_exercise -> create_plan -> activate_plan -> propose_next_session.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, insert, select

from spotter.db.schema import (
    activities,
    daily_metrics,
    exercise_session_stats,
    exercises,
    performed_sets,
    plans,
    scheduled_sessions,
)
from spotter.db.seed import seed_exercises
from spotter.garmin.client import CatalogEntry
from spotter.mcp.deps import Deps
from spotter.mcp.server import build_server
from spotter.sync.stats import rebuild_activity_stats
from spotter.units import lb_to_kg
from tests.mcp.conftest import Call, FakeGarmin
from tests.test_sync import FakeClient

CATALOG = {
    "bench": CatalogEntry("Barbell Bench Press", "BENCH_PRESS", "BARBELL_BENCH_PRESS"),
    "squat": CatalogEntry("Barbell Back Squat", "SQUAT", "BARBELL_BACK_SQUAT"),
    "deadlift": CatalogEntry("Barbell Deadlift", "DEADLIFT", "BARBELL_DEADLIFT"),
    "ohp": CatalogEntry("Barbell Overhead Press", "SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS"),
    "row": CatalogEntry("Dumbbell Row", "ROW", "DUMBBELL_ROW"),
    "pullup": CatalogEntry("Pull-up", "PULL_UP", "PULL_UP"),
}
CURATION: dict[str, dict[str, Any]] = {
    "bench": {"movement_pattern": "push_h", "primary_muscles": ["chest"],
              "secondary_muscles": ["triceps", "shoulders"], "load_type": "barbell"},
    "squat": {"movement_pattern": "squat", "primary_muscles": ["quads", "glutes"],
              "load_type": "barbell"},
    "deadlift": {"movement_pattern": "hinge", "primary_muscles": ["hamstrings", "glutes"],
                 "secondary_muscles": ["back"], "load_type": "barbell"},
    "ohp": {"movement_pattern": "push_v", "primary_muscles": ["shoulders"],
            "secondary_muscles": ["triceps"], "load_type": "barbell"},
    "row": {"movement_pattern": "pull_h", "primary_muscles": ["back"],
            "secondary_muscles": ["biceps"], "load_type": "dumbbell_each"},
    "pullup": {"movement_pattern": "pull_v", "primary_muscles": ["back"],
               "secondary_muscles": ["biceps"], "load_type": "bodyweight"},
}  # fmt: skip

START = date(2026, 9, 14)  # Monday
CHECK_DAY = datetime(2026, 10, 5, 16, 0, tzinfo=UTC)  # Monday noon in Toronto


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def garmin() -> FakeGarmin:
    return FakeGarmin(client=FakeClient([]))


@pytest.fixture
def ids(db: Engine) -> dict[str, int]:
    with db.begin() as conn:
        seed_exercises(conn, CATALOG.values())
        by_name = {
            r.garmin_name: r.id
            for r in conn.execute(select(exercises.c.id, exercises.c.garmin_name))
        }
    return {k: by_name[e.name] for k, e in CATALOG.items()}


@pytest.fixture
def mcp(ids: dict[str, int], db: Engine, garmin: FakeGarmin, clock: Clock) -> FastMCP:
    return build_server(Deps(db, db, garmin=garmin, now=clock))


Lift = tuple[str, float | None, list[int]]  # (exercise key, lb or None, reps per set)


def add_session(
    db: Engine, ids: dict[str, int], activity_id: int, day: date, lifts: list[Lift]
) -> None:
    """A strength activity at 18:00 Toronto on `day`, with its sets and stats."""
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
        rows = []
        for key, weight, reps in lifts:
            for r in reps:
                rows.append(
                    {
                        "activity_id": activity_id,
                        "set_index": len(rows),
                        "exercise_id": ids[key],
                        "garmin_category": CATALOG[key].category,
                        "garmin_name": CATALOG[key].name,
                        "reps": r,
                        "weight_kg": None if weight is None else lb_to_kg(weight, Decimal("0.5")),
                    }
                )
        conn.execute(insert(performed_sets), rows)
        rebuild_activity_stats(conn, activity_id)


def add_run(db: Engine, activity_id: int, day: date, minutes: int) -> None:
    with db.begin() as conn:
        conn.execute(
            insert(activities).values(
                garmin_activity_id=activity_id,
                type="running",
                start_time=datetime.combine(day, time(12, 0), tzinfo=UTC),
                local_date=day,
                duration_s=minutes * 60,
                raw_json={},
            )
        )


def curate_all(call: Call, ids: dict[str, int], *keys: str) -> None:
    for k in keys or tuple(CURATION):
        call("curate_exercise", exercise_id=ids[k], **CURATION[k])


def ex(
    ids: dict[str, int], key: str, role: str, sets: int, lo: int, hi: int, rir: int = 2, **kw: Any
) -> dict[str, Any]:
    return {
        "exercise_id": ids[key],
        "role": role,
        "sets": sets,
        "rep_min": lo,
        "rep_max": hi,
        "target_rir": rir,
        "rest_s": 120,
        **kw,
    }


def exit_plan(ids: dict[str, int], **kw: Any) -> dict[str, Any]:
    return {
        "name": "Exit check block",
        "rationale": "Linear main lifts for a 6-week block.",
        "weeks": 6,
        "progression_model": "linear",
        "days": [
            {
                "label": "Upper",
                "preferred_weekday": "tue",
                "exercises": [
                    ex(ids, "ohp", "main", 3, 5, 5),
                    ex(ids, "bench", "secondary", 3, 6, 8),
                    ex(ids, "row", "accessory", 3, 8, 12),
                ],
            },
            {
                "label": "Lower",
                "preferred_weekday": "thu",
                "exercises": [
                    ex(ids, "squat", "main", 3, 5, 5),
                    ex(ids, "deadlift", "secondary", 3, 8, 10, rir=2),
                ],
            },
        ],
        **kw,
    }


def seed_exit_history(db: Engine, ids: dict[str, int]) -> None:
    """The Phase 4 exit sessions (decisions.md, "Phase 4 test sessions")."""
    add_session(db, ids, 100, date(2026, 9, 7), [("deadlift", 135, [5]), ("deadlift", 315, [5, 5])])
    for week, squat in enumerate((205, 215, 225)):
        tue = START + timedelta(days=1 + 7 * week)
        last = week == 2
        add_session(
            db,
            ids,
            101 + 2 * week,
            tue,
            [
                ("ohp", 45, [5]),
                ("ohp", 115, [5, 4, 4]),
                ("bench", 95, [8]),
                ("bench", 185, [8, 8, 8] if last else [7, 7, 6]),
                ("row", 60, [10, 10, 9] if last else [9, 9, 8]),
            ],
        )
        add_session(
            db,
            ids,
            102 + 2 * week,
            tue + timedelta(days=2),
            [("squat", 95, [5]), ("squat", squat, [5, 5, 5])],
        )


@pytest.fixture
def active(call: Call, db: Engine, ids: dict[str, int], clock: Clock) -> dict[str, Any]:
    """Exit history seeded, lifts curated, the block created and activated from 09-14."""
    seed_exit_history(db, ids)
    curate_all(call, ids, "ohp", "bench", "row", "squat", "deadlift")
    clock.now = CHECK_DAY
    plan_id = call("create_plan", plan=exit_plan(ids))["plan_id"]
    return call("activate_plan", plan_id=plan_id, start_date=START.isoformat())


def session_id(plan: dict[str, Any], label: str, week: int) -> int:
    return next(
        s["scheduled_session_id"]
        for s in plan["schedule"]
        if s["label"] == label and s["week_no"] == week
    )


def by_name(proposal: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in proposal["exercises"]}


# Exit scenarios ----------------------------------------------------------------


def test_activation_links_the_sessions_already_done(active: dict[str, Any]) -> None:
    assert active["linked"] == 6
    assert active["plan"]["status"] == "active"
    assert active["plan"]["planned_end_date"] == "2026-10-25"
    sched = active["schedule"]
    assert len(sched) == 12
    assert [s["date"] for s in sched[:4]] == [
        "2026-09-15",
        "2026-09-17",
        "2026-09-22",
        "2026-09-24",
    ]
    assert [s["status"] for s in sched[:6]] == ["completed"] * 6
    assert {s["status"] for s in sched[6:]} == {"planned"}
    assert sched[0]["activity_id"] == 101


def test_exit_scenarios(call: Call, active: dict[str, Any]) -> None:
    upper = call("propose_next_session", scheduled_session_id=session_id(active, "Upper", 4))
    lower = call("propose_next_session", scheduled_session_id=session_id(active, "Lower", 4))
    assert upper["session"]["date"] == "2026-10-06"
    assert upper["session"]["readiness"] == "not_applicable"
    assert upper["session"]["deload"] is False
    up, low = by_name(upper), by_name(lower)

    squat = low["Barbell Back Squat"]  # 1: linear hit, lower body
    assert (squat["weight_lb"], squat["target_reps"], squat["rule_fired"]) == (
        235.0,
        [5, 5, 5],
        "linear:+10lb",
    )
    assert squat["model"] == "linear"
    assert squat["last"] == {"weight_lb": 225.0, "reps": [5, 5, 5]}

    row = up["Dumbbell Row"]  # 2: double progression, add reps
    assert (row["weight_lb"], row["target_reps"], row["rule_fired"]) == (
        60.0,
        [11, 11, 10],
        "double_progression:+1rep",
    )

    bench = up["Barbell Bench Press"]  # 3: double progression, add load
    assert (bench["weight_lb"], bench["target_reps"], bench["rule_fired"]) == (
        190.0,
        [6, 6, 6],
        "double_progression:+5lb",
    )
    assert bench["model"] == "double_progression"

    ohp = up["Barbell Overhead Press"]  # 4: stall reset
    assert (ohp["weight_lb"], ohp["target_reps"], ohp["rule_fired"]) == (
        100.0,
        [5, 5, 5],
        "reset:-10%",
    )
    assert ohp["flags"] == ["stall"]
    assert "misses:3" in ohp["reasons"]

    deadlift = low["Barbell Deadlift"]  # 5: estimate from history
    assert (deadlift["weight_lb"], deadlift["target_reps"], deadlift["rule_fired"]) == (
        275.0,
        [8, 8, 8],
        "estimate_from_history",
    )
    assert deadlift["reasons"] == ["e1rm_lb:367.5", "solved_for_reps:10"]


def test_evaluate_and_snapshot_after_exit_history(call: Call, active: dict[str, Any]) -> None:
    ev = call("evaluate_plan")
    assert ev["recommendation"] == "continue"
    assert (ev["week"], ev["weeks"]) == (4, 6)
    assert (ev["sessions_due"], ev["sessions_completed"], ev["adherence"]) == (4, 4, 1.0)
    assert [r["name"] for r in ev["reset_lifts"]] == ["Barbell Overhead Press"]
    lifts = {lift["name"]: lift for lift in ev["lifts"]}
    assert lifts["Barbell Back Squat"]["first"] == {
        "date": "2026-09-17",
        "weight_lb": 205.0,
        "reps": [5, 5, 5],
    }
    assert lifts["Barbell Back Squat"]["last"]["weight_lb"] == 225.0
    assert lifts["Barbell Overhead Press"]["stall_streak"] == 3
    assert lifts["Barbell Deadlift"]["exposures"] == 0
    assert ev["goal"] is None

    flags = call("get_training_snapshot")["flags"]
    assert flags["stalls"] == [
        {
            "exercise_id": lifts["Barbell Overhead Press"]["exercise_id"],
            "name": "Barbell Overhead Press",
            "flag": "stall",
            "misses": 3,
        }
    ]
    assert "stalls_note" not in flags


# Other proposal paths ------------------------------------------------------------


def day(label: str, weekday: str, *exercises: dict[str, Any]) -> dict[str, Any]:
    return {"label": label, "preferred_weekday": weekday, "exercises": list(exercises)}


def two_week_plan(call: Call, ids: dict[str, int], days: list[dict[str, Any]], **kw: Any) -> int:
    spec = {
        "name": "Test",
        "rationale": "Test.",
        "weeks": 4,
        "progression_model": "linear",
        "days": days,
        **kw,
    }
    return int(call("create_plan", plan=spec)["plan_id"])


def test_two_misses_hold_with_stall_warning(call: Call, db: Engine, ids: dict[str, int]) -> None:
    add_session(db, ids, 1, date(2026, 9, 22), [("ohp", 115, [5, 4, 4])])
    add_session(db, ids, 2, date(2026, 9, 29), [("ohp", 115, [5, 4, 4])])
    curate_all(call, ids, "ohp")
    plan_id = two_week_plan(call, ids, [day("Upper", "tue", ex(ids, "ohp", "main", 3, 5, 5))])
    plan = call("activate_plan", plan_id=plan_id, start_date="2026-09-22")
    p = by_name(call("propose_next_session", scheduled_session_id=session_id(plan, "Upper", 3)))
    ohp = p["Barbell Overhead Press"]
    assert (ohp["weight_lb"], ohp["rule_fired"], ohp["flags"]) == (
        115.0,
        "hold:miss",
        ["stall_warning"],
    )
    stalls = call("get_training_snapshot")["flags"]["stalls"]
    assert [(s["flag"], s["misses"]) for s in stalls] == [("stall_warning", 2)]


def test_needs_calibration_without_history(call: Call, ids: dict[str, int]) -> None:
    curate_all(call, ids, "squat", "pullup")
    plan_id = two_week_plan(
        call,
        ids,
        [
            day(
                "A",
                "mon",
                ex(ids, "squat", "main", 3, 5, 5),
                ex(ids, "pullup", "accessory", 3, 6, 10),
            )
        ],
    )
    plan = call("activate_plan", plan_id=plan_id, start_date="2026-10-05")
    p = call("propose_next_session", scheduled_session_id=session_id(plan, "A", 1))
    for e in p["exercises"]:
        assert e["status"] == "needs_calibration"
        assert e["weight_lb"] is None
        assert e["reasons"] == ["no_history_12w"]
        assert e["last"] is None


def test_deload_week_from_adjustment(call: Call, db: Engine, ids: dict[str, int]) -> None:
    add_session(db, ids, 1, date(2026, 9, 28), [("bench", 200, [8, 8, 8])])
    curate_all(call, ids, "bench")
    plan_id = two_week_plan(call, ids, [day("A", "mon", ex(ids, "bench", "secondary", 4, 6, 8))])
    plan = call("activate_plan", plan_id=plan_id, start_date="2026-09-28")
    with pytest.raises(ToolError, match="week_no"):
        call(
            "record_adjustment", kind="deload", reason="Two stall warnings.", payload={"week_no": 9}
        )
    adj = call(
        "record_adjustment", kind="deload", reason="Two stall warnings.", payload={"week_no": 2}
    )
    assert adj["plan_id"] == plan_id
    p = call("propose_next_session", scheduled_session_id=session_id(plan, "A", 2))
    assert p["session"]["deload"] is True
    bench = by_name(p)["Barbell Bench Press"]
    # A.9 from the last weight: 4 sets -> 2, 200 x 0.9 = 180, RIR 2 + 2.
    assert (bench["sets"], bench["weight_lb"], bench["target_rir"], bench["rule_fired"]) == (
        2,
        180.0,
        4,
        "deload",
    )


def test_red_readiness_today_and_interference(
    call: Call, db: Engine, ids: dict[str, int], clock: Clock
) -> None:
    add_session(db, ids, 1, date(2026, 9, 28), [("squat", 225, [5, 5, 5])])
    add_run(db, 2, date(2026, 10, 4), minutes=90)
    with db.begin() as conn:
        for d in (date(2026, 10, 4), date(2026, 10, 5)):
            conn.execute(insert(daily_metrics).values(local_date=d, hrv_status="LOW", raw_json={}))
    curate_all(call, ids, "squat")
    clock.now = CHECK_DAY
    plan_id = two_week_plan(call, ids, [day("Lower", "mon", ex(ids, "squat", "main", 3, 5, 5))])
    plan = call("activate_plan", plan_id=plan_id, start_date="2026-09-28")
    p = call("propose_next_session", scheduled_session_id=session_id(plan, "Lower", 2))
    assert p["session"]["date"] == "2026-10-05"
    assert p["session"]["readiness"]["class"] == "red"
    assert p["session"]["suggest_move"] is True
    assert [f["sport"] for f in p["session"]["interference_flags"]] == ["run"]
    squat = by_name(p)["Barbell Back Squat"]
    assert squat["weight_lb"] == 225.0  # no load increase
    assert squat["sets"] == 2 and squat["target_rir"] == 3
    assert "readiness:red" in squat["modifiers"]


def test_proposal_needs_an_open_session(call: Call, active: dict[str, Any]) -> None:
    with pytest.raises(ToolError, match="completed"):
        call("propose_next_session", scheduled_session_id=session_id(active, "Upper", 1))
    with pytest.raises(ToolError, match="No scheduled session"):
        call("propose_next_session", scheduled_session_id=9999)


def test_rir_feedback_feeds_progression(call: Call, db: Engine, ids: dict[str, int]) -> None:
    add_session(db, ids, 1, date(2026, 9, 28), [("bench", 185, [8, 8, 8])])
    curate_all(call, ids, "bench")
    plan_id = two_week_plan(
        call, ids, [day("A", "mon", ex(ids, "bench", "secondary", 3, 6, 8, rir=2))]
    )
    plan = call("activate_plan", plan_id=plan_id, start_date="2026-09-28")
    with pytest.raises(ToolError, match="Unknown exercise ids"):
        call("add_session_note", text="x", rir_feedback={"9999": 1})
    n = call(
        "add_session_note",
        text="Bench was a grind.",
        rir_feedback={str(ids["bench"]): 0},
        scheduled_session_id=session_id(plan, "A", 1),
    )
    assert n["local_date"] == "2026-09-28"
    bench = by_name(call("propose_next_session", scheduled_session_id=session_id(plan, "A", 2)))[
        "Barbell Bench Press"
    ]
    assert (bench["weight_lb"], bench["rule_fired"]) == (185.0, "hold:rir_low")


# Evaluation ----------------------------------------------------------------------


def test_evaluate_adjust_and_end(call: Call, db: Engine, ids: dict[str, int], clock: Clock) -> None:
    curate_all(call, ids, "bench")
    plan_id = two_week_plan(call, ids, [day("A", "mon", ex(ids, "bench", "secondary", 3, 6, 8))])
    call("activate_plan", plan_id=plan_id, start_date="2026-09-14")
    ev = call("evaluate_plan")
    assert ev["recommendation"] == "adjust"  # 0 of 2 sessions done in the window
    assert (ev["sessions_due"], ev["sessions_completed"]) == (2, 0)
    clock.now = datetime(2026, 10, 20, 16, tzinfo=UTC)
    ev = call("evaluate_plan", plan_id=plan_id)
    assert ev["recommendation"] == "end"
    assert "weeks_done:4" in ev["reasons"]


def test_evaluate_reports_goal_progress(call: Call, db: Engine, ids: dict[str, int]) -> None:
    add_session(db, ids, 1, date(2026, 9, 28), [("squat", 225, [5, 5, 5])])
    curate_all(call, ids, "squat")
    goal = call(
        "upsert_goal",
        kind="strength",
        description="Squat 315 e1RM",
        target_exercise_id=ids["squat"],
        target_value=315,
        target_unit="e1rm",
    )
    assert goal["target_value"] == 315.0
    plan_id = two_week_plan(
        call, ids, [day("A", "mon", ex(ids, "squat", "main", 3, 5, 5))], goal_id=goal["goal_id"]
    )
    call("activate_plan", plan_id=plan_id, start_date="2026-09-28")
    g = call("evaluate_plan")["goal"]
    assert g["target_e1rm_lb"] == 315.0
    assert g["latest_e1rm_lb"] == 262.5  # 225 x (1 + 5/30)
    with pytest.raises(ToolError, match="draft"):
        draft = two_week_plan(call, ids, [day("B", "tue", ex(ids, "squat", "main", 3, 5, 5))])
        call("evaluate_plan", plan_id=draft)


# Write tools ---------------------------------------------------------------------


def test_curate_defaults_and_stats_rebuild(call: Call, db: Engine, ids: dict[str, int]) -> None:
    add_session(db, ids, 1, date(2026, 9, 28), [("bench", 185, [5, 5])])
    with db.connect() as conn:
        assert conn.execute(select(exercise_session_stats.c.best_e1rm_kg)).scalar() is None
    out = call("curate_exercise", exercise_id=ids["bench"], **CURATION["bench"])
    assert out["curated"] is True
    assert (out["increment_lb"], out["e1rm_eligible"], out["activities_rebuilt"]) == (5.0, True, 1)
    assert out["secondary_muscles"] == ["triceps", "shoulders"]
    with db.connect() as conn:
        assert conn.execute(select(exercise_session_stats.c.best_e1rm_kg)).scalar() is not None
    again = call("curate_exercise", exercise_id=ids["bench"], **CURATION["bench"])
    assert again["activities_rebuilt"] == 0

    pull = call("curate_exercise", exercise_id=ids["pullup"], **CURATION["pullup"])
    assert (pull["increment_lb"], pull["e1rm_eligible"]) == (None, False)
    with pytest.raises(ToolError, match="No exercise with id 999"):
        call("curate_exercise", exercise_id=999, **CURATION["bench"])
    with pytest.raises(ToolError):
        call(
            "curate_exercise",
            exercise_id=ids["bench"],
            movement_pattern="push_h",
            primary_muscles=["pecs"],
            load_type="barbell",
        )


def test_athlete_profile_partial_updates(call: Call) -> None:
    out = call("upsert_athlete_profile", bodyweight_lb=182.5, available_days=["mon", "wed"])
    assert out["bodyweight_lb"] == 182.5
    assert out["available_days"] == ["mon", "wed"]
    out = call("upsert_athlete_profile", session_minutes=60)
    assert (out["bodyweight_lb"], out["session_minutes"]) == (182.5, 60)
    assert call("get_training_snapshot")["athlete"]["bodyweight_lb"] == 182.5


def test_goal_create_and_update(call: Call, ids: dict[str, int]) -> None:
    with pytest.raises(ToolError, match="kind and description"):
        call("upsert_goal", target_value=100)
    g = call("upsert_goal", kind="hypertrophy", description="Bigger back")
    g2 = call("upsert_goal", goal_id=g["goal_id"], status="achieved")
    assert (g2["description"], g2["status"]) == ("Bigger back", "achieved")
    with pytest.raises(ToolError, match="No goal with id 99"):
        call("upsert_goal", goal_id=99, status="dropped")


def test_create_plan_errors(call: Call, ids: dict[str, int]) -> None:
    curate_all(call, ids, "bench")
    day = {
        "label": "A",
        "preferred_weekday": "mon",
        "exercises": [ex(ids, "squat", "main", 3, 5, 5)],
    }
    with pytest.raises(ToolError, match=r"Not curated yet: \d+ \(Barbell Back Squat\)"):
        two_week_plan(call, ids, [day])
    bad = [
        {**day, "exercises": [ex(ids, "bench", "main", 3, 8, 5)]},
        {**day, "label": "B", "exercises": [ex(ids, "bench", "main", 3, 5, 5)]},
    ]
    with pytest.raises(ToolError) as err:
        two_week_plan(call, ids, bad, deload_week=7, goal_id=42)
    msg = str(err.value)
    for part in (
        "repeated: mon",
        "deload_week must be between 1 and 4",
        "No goal with id 42",
        "rep_min 8 > rep_max 5",
    ):
        assert part in msg
    with pytest.raises(ToolError, match="Unknown exercise ids: 9999"):
        two_week_plan(
            call,
            ids,
            [{**day, "exercises": [{**ex(ids, "bench", "main", 3, 5, 5), "exercise_id": 9999}]}],
        )


def test_create_plan_warnings(call: Call, ids: dict[str, int]) -> None:
    curate_all(call, ids, "bench")
    call("upsert_athlete_profile", available_days=["tue", "thu"])
    out = call(
        "create_plan",
        plan={
            "name": "P",
            "rationale": "R",
            "weeks": 3,
            "progression_model": "rir_based",
            "days": [
                day(
                    "A",
                    "mon",
                    ex(ids, "bench", "main", 3, 5, 8, progression_override={"model": "linear"}),
                )
            ],
        },
    )
    assert out["status"] == "draft"
    assert out["weekly_sets_per_muscle"] == {"chest": 3.0, "shoulders": 1.5, "triceps": 1.5}
    w = out["warnings"]
    assert "weekly_sets_low:chest:3<8" in w
    assert "new_plan_sets:chest:3 outside 10-12" in w
    assert "block_weeks:3 outside 4-8" in w
    assert any(x.startswith("linear_rep_range:Barbell Bench Press:5-8") for x in w)
    assert "weekday_unavailable:mon" in w
    assert any(x.startswith("rir_based_without_feedback") for x in w)
    with pytest.raises(ToolError):
        call(
            "create_plan",
            plan={
                "name": "P",
                "rationale": "R",
                "weeks": 3,
                "progression_model": "periodized",
                "days": [],
            },
        )


def test_end_criteria_default_to_plan_length(call: Call, ids: dict[str, int]) -> None:
    curate_all(call, ids, "bench")
    day = {
        "label": "A",
        "preferred_weekday": "mon",
        "exercises": [ex(ids, "bench", "main", 3, 5, 5)],
    }
    plan_id = two_week_plan(call, ids, [day], weeks=8, end_criteria={"stall_lifts": 1})
    crit = call("get_plan", plan_id=plan_id)["plan"]["end_criteria"]
    assert crit == {
        "max_weeks": 8,
        "stall_lifts": 1,
        "min_adherence": 0.7,
        "adherence_window_weeks": 2,
    }


def test_activate_rules(call: Call, db: Engine, ids: dict[str, int]) -> None:
    curate_all(call, ids, "bench")
    day = {
        "label": "A",
        "preferred_weekday": "wed",
        "exercises": [ex(ids, "bench", "main", 3, 5, 5)],
    }
    first = two_week_plan(call, ids, [day])
    with pytest.raises(ToolError, match="start_date must be between 2026-09-06 and 2026-11-01"):
        call("activate_plan", plan_id=first, start_date="2026-09-01")
    plan = call("activate_plan", plan_id=first, start_date="2026-10-05")
    assert [s["date"] for s in plan["schedule"]] == [
        "2026-10-07",
        "2026-10-14",
        "2026-10-21",
        "2026-10-28",
    ]
    again = call("activate_plan", plan_id=first, start_date="2026-10-05")
    assert again["schedule"] == plan["schedule"]  # idempotent
    with pytest.raises(ToolError, match="only a draft"):
        call("activate_plan", plan_id=first, start_date="2026-10-12")

    second = two_week_plan(call, ids, [day])
    with pytest.raises(ToolError, match=f"Plan {first} .* is active"):
        call("activate_plan", plan_id=second, start_date="2026-10-05")
    out = call("activate_plan", plan_id=second, start_date="2026-10-05", replace=True)
    assert out["replaced_plan_id"] == first
    old = call("get_plan", plan_id=first)
    assert old["plan"]["status"] == "abandoned"
    assert old["plan"]["actual_end_date"] == "2026-10-04"
    assert {s["status"] for s in old["schedule"]} == {"skipped"}


def test_close_plan(call: Call, active: dict[str, Any], db: Engine, ids: dict[str, int]) -> None:
    plan_id = active["plan"]["id"]
    out = call("close_plan", plan_id=plan_id, outcome_summary="Squat up 20 lb; OHP stalled.")
    assert (out["status"], out["sessions_skipped"], out["actual_end_date"]) == (
        "completed",
        6,
        "2026-10-05",
    )
    assert call("close_plan", plan_id=plan_id, outcome_summary="again")["sessions_skipped"] == 0
    with db.connect() as conn:
        p = conn.execute(select(plans).where(plans.c.id == plan_id)).one()
        statuses = conn.execute(select(scheduled_sessions.c.status)).scalars().all()
    assert p.outcome_summary == "Squat up 20 lb; OHP stalled."
    assert sorted(set(statuses)) == ["completed", "skipped"]
    assert call("get_training_snapshot")["flags"]["missed_sessions"] == []
    with pytest.raises(ToolError, match="No active plan"):
        call("record_adjustment", kind="other", reason="x")
    draft = call("create_plan", plan=exit_plan(ids))["plan_id"]
    with pytest.raises(ToolError, match="only an active plan"):
        call("close_plan", plan_id=draft, outcome_summary="x")


@pytest.fixture(autouse=True)
def _no_garmin_calls(garmin: FakeGarmin) -> Iterator[None]:
    yield
    assert garmin.client.set_calls == []
