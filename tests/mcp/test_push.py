"""Garmin write tools in process: push_session, unschedule_session, and Garmin cleanup on
close_plan and activate_plan(replace=true), against a fake Garmin client.

Builds on the Phase 4 exit block (tests/mcp/test_planning.py): active from 09-14, checked
on Monday 10-05, so Upper W4 is Tuesday 10-06 and Lower W4 is Thursday 10-08.
"""

# The planning fixtures are imported, so test arguments shadow them by design.
# ruff: noqa: F811

from __future__ import annotations

import copy
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, func, select

from spotter.db.schema import adjustments, prescribed_sets, scheduled_sessions
from spotter.garmin.errors import GarminNotFound, GarminUnavailable
from spotter.units import lb_to_kg
from tests.mcp.conftest import Call, FakeGarmin
from tests.mcp.test_planning import (  # noqa: F401  (fixtures)
    START,
    Clock,
    active,
    by_name,
    clock,
    curate_all,
    exit_plan,
    ids,
    mcp,
    session_id,
)
from tests.test_sync import FakeClient, _load, strength


class FakeWriter(FakeClient):
    """Records workout writes. Ids start at 9000. `fail[name]` raises once on that call."""

    def __init__(self) -> None:
        super().__init__([])
        self.calls: list[tuple[str, Any]] = []
        self.workouts: dict[int, dict[str, Any]] = {}
        self.schedules: dict[int, tuple[int, date]] = {}
        self.next_id = 9000
        self.fail: dict[str, Exception] = {}

    def _step(self, name: str, arg: Any) -> None:
        self.calls.append((name, arg))
        if name in self.fail:
            raise self.fail.pop(name)

    def _new_id(self) -> int:
        self.next_id += 1
        return self.next_id

    def upload_workout(self, payload: dict[str, Any]) -> int:
        self._step("upload", payload["workoutName"])
        workout_id = self._new_id()
        self.workouts[workout_id] = copy.deepcopy(payload)
        return workout_id

    def update_workout(self, workout_id: int, payload: dict[str, Any]) -> None:
        self._step("update", workout_id)
        if workout_id not in self.workouts:
            raise GarminNotFound("Garmin has no such item.")
        self.workouts[workout_id] = copy.deepcopy(payload)

    def schedule_workout(self, workout_id: int, day: date) -> int:
        self._step("schedule", workout_id)
        schedule_id = self._new_id()
        self.schedules[schedule_id] = (workout_id, day)
        return schedule_id

    def unschedule_workout(self, schedule_id: int) -> bool:
        self._step("unschedule", schedule_id)
        return self.schedules.pop(schedule_id, None) is not None

    def delete_workout(self, workout_id: int) -> bool:
        self._step("delete", workout_id)
        return self.workouts.pop(workout_id, None) is not None

    def add_strength(self, activity_id: int, start_gmt: str, workout_id: int | None) -> None:
        summary = strength(activity_id, start_gmt)
        if workout_id is not None:
            summary["workoutId"] = workout_id
        self.summaries.append(summary)
        self.sets[activity_id] = copy.deepcopy(_load("exercise_sets.json"))

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


@pytest.fixture
def writer() -> FakeWriter:
    return FakeWriter()


@pytest.fixture
def garmin(writer: FakeWriter) -> FakeGarmin:
    return FakeGarmin(client=writer)


def _session(db: Engine, sid: int) -> Any:
    with db.connect() as conn:
        return conn.execute(select(scheduled_sessions).where(scheduled_sessions.c.id == sid)).one()


def _prescribed(db: Engine, sid: int) -> list[Any]:
    with db.connect() as conn:
        return conn.execute(
            select(prescribed_sets)
            .where(prescribed_sets.c.scheduled_session_id == sid)
            .order_by(prescribed_sets.c.id)
        ).all()


def _swaps(db: Engine) -> int:
    with db.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(adjustments).where(adjustments.c.kind == "swap")
        ).scalar_one()


def upper(active: dict[str, Any]) -> int:
    return session_id(active, "Upper", 4)


# push_session -------------------------------------------------------------------


def test_push_sends_the_proposal(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter, ids: dict[str, int]
) -> None:
    sid = upper(active)
    proposal = by_name(call("propose_next_session", scheduled_session_id=sid))
    out = call("push_session", scheduled_session_id=sid)

    assert writer.names() == ["upload", "schedule"]
    assert out["action"] == "created"
    assert out["session"]["status"] == "pushed"
    assert out["workout_name"] == "Upper W4 2026-10-06"
    assert out["description"] == "Barbell Overhead Press -15 lb. Barbell Bench Press +5 lb."
    pushed = {e["name"]: e for e in out["exercises"]}
    assert pushed["Barbell Overhead Press"]["sets"] == [{"reps": 5, "weight_lb": 100.0}] * 3
    assert [s["reps"] for s in pushed["Dumbbell Row"]["sets"]] == [11, 11, 10]
    assert (
        pushed["Barbell Bench Press"]["rule_fired"] == proposal["Barbell Bench Press"]["rule_fired"]
    )
    # OHP and bench are repeat groups (2 steps x 3); the row is 3 sets + 2 rests.
    assert out["steps"] == 6 + 6 + 5

    row = _session(db, sid)
    assert row.status == "pushed"
    assert row.garmin_workout_id == out["garmin_workout_id"]
    assert writer.schedules[row.garmin_schedule_id] == (row.garmin_workout_id, date(2026, 10, 6))
    sets = _prescribed(db, sid)
    assert len(sets) == 9
    ohp = [s for s in sets if s.exercise_id == ids["ohp"]]
    assert [s.target_weight_kg for s in ohp] == [lb_to_kg(100, 5)] * 3
    assert {s.rule_fired for s in ohp} == {"reset:-10%"}


def test_repush_updates_the_same_workout(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter, ids: dict[str, int]
) -> None:
    sid = upper(active)
    first = call("push_session", scheduled_session_id=sid)
    writer.calls.clear()
    second = call(
        "push_session",
        scheduled_session_id=sid,
        exercises=[
            {"exercise_id": ids["ohp"], "sets": [{"reps": 5, "weight_lb": 105}] * 3},
            {"exercise_id": ids["bench"], "sets": [{"reps": 6, "weight_lb": 190}] * 3},
        ],
    )
    assert writer.names() == ["update"]
    assert second["action"] == "updated"
    assert second["garmin_workout_id"] == first["garmin_workout_id"]
    assert second["garmin_schedule_id"] == first["garmin_schedule_id"]
    assert len(writer.workouts) == 1 and len(writer.schedules) == 1
    rules = {e["name"]: e["rule_fired"] for e in second["exercises"]}
    assert rules == {
        "Barbell Overhead Press": "manual",
        "Barbell Bench Press": "double_progression:+5lb",
    }
    assert [e["name"] for e in second["not_pushed"]] == ["Dumbbell Row"]
    sets = _prescribed(db, sid)
    assert len(sets) == 6
    assert {s.target_weight_kg for s in sets if s.exercise_id == ids["ohp"]} == {lb_to_kg(105, 5)}


def test_dry_run_sends_and_saves_nothing(
    call: Call, active: dict[str, Any], db: Engine, garmin: FakeGarmin
) -> None:
    sid = upper(active)
    opens = garmin.opens
    out = call("push_session", scheduled_session_id=sid, dry_run=True)
    assert out["dry_run"] is True
    assert out["steps"] == 17
    assert "garmin_workout_id" not in out
    assert garmin.opens == opens
    assert _session(db, sid).status == "planned"
    assert _prescribed(db, sid) == []


def test_needs_calibration_blocks_the_default_push(
    call: Call, db: Engine, ids: dict[str, int], clock: Clock, writer: FakeWriter
) -> None:
    curate_all(call, ids, "ohp", "bench", "row", "squat", "deadlift")
    clock.now = datetime(2026, 10, 5, 16, 0, tzinfo=clock.now.tzinfo)
    plan_id = call("create_plan", plan=exit_plan(ids))["plan_id"]
    plan = call("activate_plan", plan_id=plan_id, start_date=START.isoformat())
    sid = session_id(plan, "Upper", 4)
    with pytest.raises(ToolError, match="needs_calibration"):
        call("push_session", scheduled_session_id=sid)
    assert writer.calls == []


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"exercise_id": "pullup", "sets": [{"reps": 8}]}, "Not curated"),
        ({"exercise_id": "bench", "sets": [{"reps": 8}]}, "needs a weight"),
        ({"exercise_id": 999999, "sets": [{"reps": 8}]}, "Unknown exercise"),
    ],
)
def test_push_rejects_bad_exercises(
    call: Call, active: dict[str, Any], ids: dict[str, int], change: dict[str, Any], message: str
) -> None:
    exercise = {**change, "exercise_id": ids.get(change["exercise_id"], change["exercise_id"])}
    with pytest.raises(ToolError, match=message):
        call("push_session", scheduled_session_id=upper(active), exercises=[exercise])


def test_weight_on_a_bodyweight_exercise_is_rejected(
    call: Call, active: dict[str, Any], ids: dict[str, int]
) -> None:
    curate_all(call, ids, "pullup")
    exercise = {"exercise_id": ids["pullup"], "sets": [{"reps": 8, "weight_lb": 25}], "rest_s": 90}
    with pytest.raises(ToolError, match="no load increment"):
        call("push_session", scheduled_session_id=upper(active), exercises=[exercise])


def test_past_and_completed_sessions_are_refused(call: Call, active: dict[str, Any]) -> None:
    with pytest.raises(ToolError, match="completed"):
        call("push_session", scheduled_session_id=session_id(active, "Upper", 1))


def test_past_open_session_is_refused(call: Call, active: dict[str, Any], clock: Clock) -> None:
    clock.now = datetime(2026, 10, 7, 16, 0, tzinfo=clock.now.tzinfo)
    with pytest.raises(ToolError, match="was due 2026-10-06"):
        call("push_session", scheduled_session_id=upper(active))


def test_weights_round_to_the_increment(
    call: Call, active: dict[str, Any], ids: dict[str, int]
) -> None:
    out = call(
        "push_session",
        scheduled_session_id=upper(active),
        dry_run=True,
        exercises=[
            {"exercise_id": ids["bench"], "sets": [{"reps": 6, "weight_lb": 187}] * 2},
            {"exercise_id": ids["ohp"], "sets": [{"reps": 5, "weight_lb": 30}]},
        ],
    )
    assert out["rounded"] == [
        {"exercise_id": ids["bench"], "given_lb": 187.0, "pushed_lb": 185.0},
        {"exercise_id": ids["bench"], "given_lb": 187.0, "pushed_lb": 185.0},
        {"exercise_id": ids["ohp"], "given_lb": 30.0, "pushed_lb": 45.0},
    ]
    assert out["exercises"][1]["sets"] == [{"reps": 5, "weight_lb": 45.0}]


def test_off_plan_exercise_is_logged_once(
    call: Call, active: dict[str, Any], db: Engine, ids: dict[str, int]
) -> None:
    curate_all(call, ids, "pullup")
    sid = upper(active)
    exercises = [
        {"exercise_id": ids["ohp"], "sets": [{"reps": 5, "weight_lb": 100}] * 3},
        {"exercise_id": ids["pullup"], "sets": [{"reps": 8}] * 3, "rest_s": 90},
    ]
    for _ in range(2):
        out = call("push_session", scheduled_session_id=sid, exercises=exercises)
    assert out["off_plan"] == [{"exercise_id": ids["pullup"], "name": "Pull-up"}]
    assert out["exercises"][1]["rule_fired"] == "off_plan"
    assert _swaps(db) == 1
    pullups = [s for s in _prescribed(db, sid) if s.exercise_id == ids["pullup"]]
    assert [s.target_weight_kg for s in pullups] == [None] * 3


def test_off_plan_exercise_needs_rest(
    call: Call, active: dict[str, Any], ids: dict[str, int]
) -> None:
    curate_all(call, ids, "pullup")
    with pytest.raises(ToolError, match="pass its rest_s"):
        call(
            "push_session",
            scheduled_session_id=upper(active),
            exercises=[{"exercise_id": ids["pullup"], "sets": [{"reps": 8}]}],
        )


def test_workout_deleted_in_garmin_is_uploaded_again(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter
) -> None:
    sid = upper(active)
    first = call("push_session", scheduled_session_id=sid)
    writer.workouts.clear()
    writer.schedules.clear()
    writer.calls.clear()
    second = call("push_session", scheduled_session_id=sid)
    assert writer.names() == ["update", "unschedule", "upload", "schedule"]
    assert second["action"] == "created"
    assert second["garmin_workout_id"] != first["garmin_workout_id"]
    assert _session(db, sid).garmin_schedule_id == second["garmin_schedule_id"]


def test_failure_after_upload_keeps_the_workout_id(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter
) -> None:
    sid = upper(active)
    writer.fail["schedule"] = GarminUnavailable("Garmin could not be reached. Try again later.")
    with pytest.raises(ToolError, match="safe to retry"):
        call("push_session", scheduled_session_id=sid)
    row = _session(db, sid)
    assert row.status == "planned"
    assert row.garmin_workout_id is not None and row.garmin_schedule_id is None
    assert _prescribed(db, sid) == []

    writer.calls.clear()
    out = call("push_session", scheduled_session_id=sid)
    assert writer.names() == ["update", "schedule"]
    assert out["garmin_workout_id"] == row.garmin_workout_id
    assert len(writer.workouts) == 1 and len(writer.schedules) == 1
    assert _session(db, sid).status == "pushed"


# unschedule_session --------------------------------------------------------------


def test_unschedule_pushed_session_removes_it_from_garmin(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter
) -> None:
    sid = upper(active)
    call("push_session", scheduled_session_id=sid)
    writer.calls.clear()
    out = call("unschedule_session", scheduled_session_id=sid, reason="Travel")
    assert writer.names() == ["unschedule", "delete"]
    assert out == {"session": {**out["session"], "status": "skipped"}, "removed_from_garmin": True}
    row = _session(db, sid)
    assert row.garmin_workout_id is None and row.garmin_schedule_id is None
    assert _prescribed(db, sid) == []
    assert writer.workouts == {} and writer.schedules == {}
    plan = call("get_plan")
    assert plan["adjustments"][-1]["kind"] == "other"
    assert plan["adjustments"][-1]["reason"] == "Travel"


def test_unschedule_planned_session_skips_garmin(
    call: Call, active: dict[str, Any], garmin: FakeGarmin
) -> None:
    opens = garmin.opens
    out = call("unschedule_session", scheduled_session_id=upper(active), reason="Sick")
    assert out["removed_from_garmin"] is False
    assert out["session"]["status"] == "skipped"
    assert garmin.opens == opens


def test_unschedule_completed_session_is_refused(call: Call, active: dict[str, Any]) -> None:
    with pytest.raises(ToolError, match="completed"):
        call("unschedule_session", scheduled_session_id=session_id(active, "Upper", 1), reason="x")


def test_move_returns_the_session_to_planned(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter
) -> None:
    sid = upper(active)
    call("push_session", scheduled_session_id=sid)
    out = call("unschedule_session", scheduled_session_id=sid, reason="Busy", new_date="2026-10-07")
    assert out["session"]["status"] == "planned"
    assert out["session"]["date"] == "2026-10-07"
    assert out["session"]["week_no"] == 4
    assert call("get_plan")["adjustments"][-1]["payload"] == {
        "scheduled_session_id": sid,
        "from": "2026-10-06",
        "to": "2026-10-07",
    }
    again = call("push_session", scheduled_session_id=sid)
    assert again["action"] == "created"
    assert again["workout_name"] == "Upper W4 2026-10-07"
    assert list(writer.schedules.values()) == [(again["garmin_workout_id"], date(2026, 10, 7))]


def test_move_a_future_session_to_today_then_push(
    call: Call, active: dict[str, Any], writer: FakeWriter
) -> None:
    sid = upper(active)
    call("push_session", scheduled_session_id=sid)
    writer.calls.clear()
    call("unschedule_session", scheduled_session_id=sid, reason="Free today", new_date="2026-10-05")
    assert writer.names() == ["unschedule", "delete"]  # no sync: it was not due yet
    out = call("push_session", scheduled_session_id=sid)
    assert out["session"]["date"] == "2026-10-05"
    assert out["session"]["status"] == "pushed"


def test_move_past_the_plan_end_is_refused(call: Call, active: dict[str, Any]) -> None:
    with pytest.raises(ToolError, match="plan end 2026-10-25"):
        call(
            "unschedule_session",
            scheduled_session_id=upper(active),
            reason="x",
            new_date="2026-10-26",
        )


def test_trained_past_session_is_not_removed(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter, clock: Clock
) -> None:
    sid = upper(active)
    pushed = call("push_session", scheduled_session_id=sid)
    clock.now = datetime(2026, 10, 7, 16, 0, tzinfo=clock.now.tzinfo)
    # Trained on the 6th from the watch, not synced yet.
    writer.add_strength(500, "2026-10-06 22:00:00", pushed["garmin_workout_id"])
    writer.calls.clear()
    with pytest.raises(ToolError, match="trained and synced \\(activity 500\\)"):
        call("unschedule_session", scheduled_session_id=sid, reason="Missed it")
    assert "delete" not in writer.names()
    row = _session(db, sid)
    assert row.status == "completed" and row.activity_id == 500
    assert len(writer.workouts) == 1


def test_untrained_past_session_is_removed_after_sync(
    call: Call, active: dict[str, Any], writer: FakeWriter, clock: Clock
) -> None:
    sid = upper(active)
    call("push_session", scheduled_session_id=sid)
    clock.now = datetime(2026, 10, 7, 16, 0, tzinfo=clock.now.tzinfo)
    writer.calls.clear()
    out = call("unschedule_session", scheduled_session_id=sid, reason="Missed it")
    assert out["removed_from_garmin"] is True
    assert writer.names() == ["unschedule", "delete"]
    assert writer.windows  # the sync ran first


# close_plan and activate_plan(replace=true) ---------------------------------------


def test_close_removes_future_pushed_sessions_and_keeps_past_ones(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter, clock: Clock
) -> None:
    past, future = upper(active), session_id(active, "Lower", 4)
    call("push_session", scheduled_session_id=past)
    kept = call("push_session", scheduled_session_id=future)
    clock.now = datetime(2026, 10, 7, 16, 0, tzinfo=clock.now.tzinfo)  # Upper W4 is past
    writer.calls.clear()
    out = call("close_plan", plan_id=active["plan"]["id"], outcome_summary="Done early")
    assert out["status"] == "completed"
    assert writer.names() == ["unschedule", "delete"]
    assert writer.calls[1] == ("delete", kept["garmin_workout_id"])
    assert _session(db, future).garmin_workout_id is None
    assert _prescribed(db, future) == []
    assert _session(db, past).garmin_workout_id is not None
    assert {_session(db, past).status, _session(db, future).status} == {"skipped"}


def test_garmin_failure_leaves_the_plan_active(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter
) -> None:
    sid = upper(active)
    call("push_session", scheduled_session_id=sid)
    writer.fail["delete"] = GarminUnavailable("Garmin could not be reached. Try again later.")
    with pytest.raises(ToolError, match="safe to retry"):
        call("close_plan", plan_id=active["plan"]["id"], outcome_summary="x")
    assert call("get_plan")["plan"]["status"] == "active"
    assert _session(db, sid).status == "pushed"

    out = call("close_plan", plan_id=active["plan"]["id"], outcome_summary="x")
    assert out["status"] == "completed"
    assert writer.workouts == {}


def test_replace_removes_pushed_sessions(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter, ids: dict[str, int]
) -> None:
    sid = upper(active)
    call("push_session", scheduled_session_id=sid)
    draft = call("create_plan", plan=exit_plan(ids, name="Next block"))["plan_id"]
    out = call("activate_plan", plan_id=draft, start_date="2026-10-12", replace=True)
    assert out["replaced_plan_id"] == active["plan"]["id"]
    assert writer.workouts == {} and writer.schedules == {}
    assert _session(db, sid).status == "skipped"


def test_close_without_pushed_sessions_opens_no_garmin(
    call: Call, active: dict[str, Any], garmin: FakeGarmin
) -> None:
    opens = garmin.opens
    call("close_plan", plan_id=active["plan"]["id"], outcome_summary="x")
    assert garmin.opens == opens


def test_bad_replace_removes_nothing(
    call: Call, active: dict[str, Any], writer: FakeWriter, ids: dict[str, int]
) -> None:
    call("push_session", scheduled_session_id=upper(active))
    draft = call("create_plan", plan=exit_plan(ids, name="Next block"))["plan_id"]
    with pytest.raises(ToolError, match="start_date must be"):
        call("activate_plan", plan_id=draft, start_date="2027-01-01", replace=True)
    assert len(writer.workouts) == 1


# Planned vs actual ----------------------------------------------------------------


def test_snapshot_shows_planned_vs_actual(
    call: Call, active: dict[str, Any], writer: FakeWriter, clock: Clock
) -> None:
    sid = upper(active)
    pushed = call("push_session", scheduled_session_id=sid)
    clock.now = datetime(2026, 10, 7, 16, 0, tzinfo=clock.now.tzinfo)
    writer.add_strength(500, "2026-10-06 22:00:00", pushed["garmin_workout_id"])
    snap = call("get_training_snapshot", force_sync=True)
    planned = snap["recent_strength"][0]["planned"]
    assert planned["scheduled_session_id"] == sid
    assert planned["label"] == "Upper" and planned["week_no"] == 4
    assert planned["pushed"] is True
    # The fixture's sets are bench only: bench is judged, OHP and the row were not done.
    [bench] = planned["exercises"]
    assert bench["name"] == "Barbell Bench Press"
    assert bench["prescribed"] == [{"reps": 6, "weight_lb": 190.0}] * 3
    assert isinstance(bench["met_prescription"], bool)
    assert {e["name"] for e in planned["not_done"]} == {"Barbell Overhead Press", "Dumbbell Row"}
    assert planned["off_plan"] == []
    rows = {s["scheduled_session_id"]: s for s in call("get_plan")["schedule"]}
    assert rows[sid]["status"] == "completed"
    assert rows[sid]["pushed"] is True and rows[sid]["prescribed"] is True


def test_date_linked_session_uses_the_plan_day(call: Call, active: dict[str, Any]) -> None:
    snap = call("get_training_snapshot")
    planned = snap["recent_strength"][0]["planned"]
    assert planned["label"] == "Lower" and planned["week_no"] == 3
    assert planned["pushed"] is False
    assert [(e["name"], e["prescribed"]) for e in planned["exercises"]] == [
        ("Barbell Back Squat", None)
    ]
    assert planned["not_done"] == [
        {"exercise_id": planned["not_done"][0]["exercise_id"], "name": "Barbell Deadlift"}
    ]
    row = next(
        s for s in call("get_plan")["schedule"] if s["scheduled_session_id"] == upper(active)
    )
    assert row["pushed"] is False and row["prescribed"] is False


def test_prescribed_weights_are_stored_in_kg(
    call: Call, active: dict[str, Any], db: Engine, ids: dict[str, int]
) -> None:
    sid = upper(active)
    call(
        "push_session",
        scheduled_session_id=sid,
        exercises=[{"exercise_id": ids["row"], "sets": [{"reps": 10, "weight_lb": 62.5}]}],
    )
    [row] = _prescribed(db, sid)
    assert row.target_weight_kg == lb_to_kg(Decimal(65), 5)  # 62.5 rounds half up
