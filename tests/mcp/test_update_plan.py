"""update_plan in process, against a fake Garmin client.

Builds on the Phase 4 exit block (tests/mcp/test_planning.py): active from Monday 09-14,
Upper on Tuesday and Lower on Thursday, checked on Monday 10-05 (week 4). Weeks 1 to 3 are
history; Upper W4 is 10-06 and Lower W4 is 10-08.
"""

# The planning fixtures are imported, so test arguments shadow them by design.
# ruff: noqa: F811

from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastmcp.exceptions import ToolError
from sqlalchemy import Engine, func, select

from spotter.db.schema import adjustments, plan_days, scheduled_sessions
from spotter.garmin.errors import GarminUnavailable
from spotter.mcp.tools.plan_update import DayPlan, Session, rebuild_schedule
from tests.mcp.conftest import Call, FakeGarmin
from tests.mcp.test_planning import (  # noqa: F401  (fixtures)
    START,
    Clock,
    active,
    clock,
    curate_all,
    ex,
    exit_plan,
    ids,
    mcp,
    session_id,
)
from tests.mcp.test_push import FakeWriter, garmin, writer  # noqa: F401  (fixtures)

KEYS = (
    "exercise_id",
    "role",
    "sets",
    "rep_min",
    "rep_max",
    "hold_min_s",
    "hold_max_s",
    "target_rir",
    "rest_s",
    "superset_group",
    "progression_override",
)


def spec_of(detail: dict[str, Any]) -> dict[str, Any]:
    """get_plan output back into an update_plan spec, as Claude builds it."""
    p = detail["plan"]
    return {
        "name": p["name"],
        "rationale": p["rationale"],
        "weeks": p["weeks"],
        "progression_model": p["progression_model"],
        "deload_week": p["deload_week"],
        "goal_id": p["goal_id"],
        "end_criteria": p["end_criteria"],
        "days": [
            {
                "plan_day_id": d["plan_day_id"],
                "label": d["label"],
                "preferred_weekday": d["preferred_weekday"],
                "exercises": [{k: e[k] for k in KEYS if k in e} for e in d["exercises"]],
            }
            for d in detail["days"]
        ],
    }


def update(call: Call, plan: dict[str, Any], **kw: Any) -> dict[str, Any]:
    return call("update_plan", plan_id=plan["plan"]["id"], plan=plan["spec"], reason="test", **kw)


def edit(call: Call, active: dict[str, Any]) -> dict[str, Any]:
    """The live plan and an editable copy of its spec."""
    detail = call("get_plan", plan_id=active["plan"]["id"])
    return {**detail, "spec": copy.deepcopy(spec_of(detail))}


def day_of(plan: dict[str, Any], label: str) -> dict[str, Any]:
    return next(d for d in plan["spec"]["days"] if d["label"] == label)


def dates(detail: dict[str, Any], label: str) -> dict[int, str]:
    return {s["week_no"]: s["date"] for s in detail["schedule"] if s["label"] == label}


def _updates(db: Engine) -> int:
    with db.connect() as conn:
        return conn.execute(
            select(func.count()).select_from(adjustments).where(adjustments.c.kind == "plan_update")
        ).scalar_one()


def _session(db: Engine, sid: int) -> Any:
    with db.connect() as conn:
        return conn.execute(select(scheduled_sessions).where(scheduled_sessions.c.id == sid)).one()


# No change ---------------------------------------------------------------------


def test_same_plan_changes_nothing(
    call: Call, active: dict[str, Any], db: Engine, garmin: FakeGarmin
) -> None:
    call("push_session", scheduled_session_id=session_id(active, "Upper", 4))
    opens = garmin.opens
    plan = edit(call, active)
    out = update(call, plan)
    assert out["changed"] is False
    assert out["changes"]["removed_from_garmin"] == []
    assert garmin.opens == opens
    assert _updates(db) == 0
    assert call("get_plan")["schedule"] == plan["schedule"]


def test_dry_run_reports_and_writes_nothing(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter
) -> None:
    upper_w4 = session_id(active, "Upper", 4)
    call("push_session", scheduled_session_id=upper_w4)
    writer.calls.clear()
    plan = edit(call, active)
    day_of(plan, "Upper")["preferred_weekday"] = "wed"
    out = update(call, plan, dry_run=True)
    assert out["dry_run"] is True and out["changed"] is True
    assert out["changes"]["days_changed"] == ["Upper"]
    assert [s["to"] for s in out["changes"]["sessions_moved"]] == [
        "2026-10-07",
        "2026-10-14",
        "2026-10-21",
    ]
    assert out["changes"]["removed_from_garmin"] == [upper_w4]
    assert writer.calls == []
    assert _updates(db) == 0
    assert call("get_plan")["schedule"] == plan["schedule"]


# Exercises and Garmin ----------------------------------------------------------


def test_swap_removes_the_stale_pushed_workout(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter, ids: dict[str, int]
) -> None:
    upper_w4, lower_w4 = session_id(active, "Upper", 4), session_id(active, "Lower", 4)
    call("push_session", scheduled_session_id=upper_w4)
    call("push_session", scheduled_session_id=lower_w4)
    writer.calls.clear()
    curate_all(call, ids, "pullup")
    plan = edit(call, active)
    day_of(plan, "Upper")["exercises"][2] = ex(ids, "pullup", "accessory", 3, 6, 10)

    out = update(call, plan)
    assert out["changes"]["days_changed"] == ["Upper"]
    assert out["changes"]["removed_from_garmin"] == [upper_w4]
    assert writer.names() == ["unschedule", "delete"]
    row = _session(db, upper_w4)
    assert (row.status, row.garmin_workout_id, row.garmin_schedule_id) == ("planned", None, None)
    assert _session(db, lower_w4).status == "pushed"
    assert _updates(db) == 1

    detail = call("get_plan")
    upper = next(d for d in detail["days"] if d["label"] == "Upper")
    assert upper["plan_day_id"] == day_of(plan, "Upper")["plan_day_id"]
    assert [e["exercise_id"] for e in upper["exercises"]][-1] == ids["pullup"]
    proposal = call("propose_next_session", scheduled_session_id=upper_w4)
    assert "Pull-up" in [e["name"] for e in proposal["exercises"]]
    # A repeat is a no-op.
    assert update(call, {**plan, "plan": detail["plan"]})["changed"] is False


def test_todays_session_is_history(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter, clock: Clock
) -> None:
    clock.now = datetime(2026, 10, 6, 16, 0, tzinfo=UTC)  # Tuesday: Upper W4 is today
    upper_w4 = session_id(active, "Upper", 4)
    call("push_session", scheduled_session_id=upper_w4)
    writer.calls.clear()
    plan = edit(call, active)
    day_of(plan, "Upper")["exercises"][0]["sets"] = 4
    out = update(call, plan)
    assert out["changes"]["days_changed"] == ["Upper"]
    assert out["changes"]["removed_from_garmin"] == []
    assert writer.calls == []
    assert _session(db, upper_w4).status == "pushed"


def test_model_change_removes_every_pushed_workout(
    call: Call, active: dict[str, Any], writer: FakeWriter
) -> None:
    pushed = sorted([session_id(active, "Upper", 4), session_id(active, "Lower", 4)])
    for sid in pushed:
        call("push_session", scheduled_session_id=sid)
    plan = edit(call, active)
    plan["spec"]["progression_model"] = "double_progression"
    out = update(call, plan)
    assert out["changes"]["plan"] == ["progression_model"]
    assert out["changes"]["days_changed"] == []
    assert out["changes"]["removed_from_garmin"] == pushed


def test_garmin_failure_changes_nothing(
    call: Call, active: dict[str, Any], db: Engine, writer: FakeWriter
) -> None:
    upper_w4 = session_id(active, "Upper", 4)
    call("push_session", scheduled_session_id=upper_w4)
    plan = edit(call, active)
    day_of(plan, "Upper")["preferred_weekday"] = "wed"
    writer.fail["delete"] = GarminUnavailable("Garmin could not be reached. Try again later.")
    with pytest.raises(ToolError, match="safe to retry"):
        update(call, plan)
    assert call("get_plan")["schedule"] == plan["schedule"]
    assert _updates(db) == 0

    out = update(call, plan)
    assert out["changes"]["removed_from_garmin"] == [upper_w4]
    assert _session(db, upper_w4).garmin_workout_id is None


# Schedule ----------------------------------------------------------------------


def test_weekday_move_keeps_reschedules_and_history(call: Call, active: dict[str, Any]) -> None:
    upper_w5 = session_id(active, "Upper", 5)
    call(
        "unschedule_session",
        scheduled_session_id=upper_w5,
        reason="travel",
        new_date="2026-10-14",
    )
    plan = edit(call, active)
    day_of(plan, "Lower")["preferred_weekday"] = "fri"
    out = update(call, plan)
    assert out["changes"]["days_changed"] == ["Lower"]

    detail = call("get_plan")
    assert dates(detail, "Lower") == {
        1: "2026-09-17",
        2: "2026-09-24",
        3: "2026-10-01",
        4: "2026-10-09",
        5: "2026-10-16",
        6: "2026-10-23",
    }
    assert dates(detail, "Upper")[5] == "2026-10-14"  # Upper did not move

    # Upper to Monday: week 4's Monday is today, so W4 keeps Tuesday.
    plan = edit(call, active)
    day_of(plan, "Upper")["preferred_weekday"] = "mon"
    update(call, plan)
    assert dates(call("get_plan"), "Upper") == {
        1: "2026-09-15",
        2: "2026-09-22",
        3: "2026-09-29",
        4: "2026-10-06",
        5: "2026-10-12",
        6: "2026-10-19",
    }


def test_new_day_gets_future_sessions_only(
    call: Call, active: dict[str, Any], ids: dict[str, int]
) -> None:
    curate_all(call, ids, "pullup")
    plan = edit(call, active)
    plan["spec"]["days"].append(
        {
            "label": "Pull",
            "preferred_weekday": "mon",
            "exercises": [ex(ids, "pullup", "main", 3, 5, 8)],
        }
    )
    out = update(call, plan)
    assert out["changes"]["days_added"] == ["Pull"]
    assert [s["date"] for s in out["changes"]["sessions_added"]] == ["2026-10-12", "2026-10-19"]
    detail = call("get_plan")
    assert detail["plan"]["sessions_per_week"] == 3
    assert [d["label"] for d in detail["days"]] == ["Upper", "Lower", "Pull"]


def test_removed_day_keeps_its_history(
    call: Call, active: dict[str, Any], db: Engine, ids: dict[str, int]
) -> None:
    lower_id = day_of(edit(call, active), "Lower")["plan_day_id"]
    plan = edit(call, active)
    plan["spec"]["days"] = [day_of(plan, "Upper")]
    out = update(call, plan)
    assert out["changes"]["days_removed"] == ["Lower"]
    assert [s["week_no"] for s in out["changes"]["sessions_removed"]] == [4, 5, 6]

    detail = call("get_plan")
    assert [d["label"] for d in detail["days"]] == ["Upper"]
    assert detail["plan"]["sessions_per_week"] == 1
    assert sorted(dates(detail, "Lower")) == [1, 2, 3]
    with db.connect() as conn:
        assert conn.execute(
            select(plan_days.c.removed_on).where(plan_days.c.id == lower_id)
        ).scalar_one() == date(2026, 10, 5)
    lifts = {lift["name"] for lift in call("evaluate_plan")["lifts"]}
    assert "Barbell Back Squat" not in lifts
    assert update(call, edit(call, active))["changed"] is False

    # A day added later takes the next order before the removed one.
    plan = edit(call, active)
    plan["spec"]["days"].append({**copy.deepcopy(day_of(plan, "Upper")), "plan_day_id": None})
    plan["spec"]["days"][-1].update(label="Upper B", preferred_weekday="fri")
    update(call, plan)
    with db.connect() as conn:
        orders = dict(
            conn.execute(
                select(plan_days.c.label, plan_days.c.day_order).where(
                    plan_days.c.plan_id == active["plan"]["id"]
                )
            ).all()
        )
    assert orders == {"Upper": 1, "Upper B": 2, "Lower": 3}


def test_weeks_extend_and_shrink(call: Call, active: dict[str, Any]) -> None:
    plan = edit(call, active)
    plan["spec"]["weeks"] = 8
    plan["spec"]["end_criteria"]["max_weeks"] = 8
    out = update(call, plan)
    assert len(out["changes"]["sessions_added"]) == 4
    assert out["changes"]["plan"] == ["weeks", "end_criteria"]
    assert call("get_plan")["plan"]["planned_end_date"] == "2026-11-08"

    plan = edit(call, active)
    plan["spec"]["weeks"] = 5
    out = update(call, plan)
    assert [s["week_no"] for s in out["changes"]["sessions_removed"]] == [6, 6, 7, 7, 8, 8]
    assert call("get_plan")["plan"]["planned_end_date"] == "2026-10-18"

    plan = edit(call, active)
    plan["spec"]["weeks"] = 3
    with pytest.raises(ToolError, match="at least 4, the current week"):
        update(call, plan)


# Drafts and errors -------------------------------------------------------------


def test_draft_edit_deletes_removed_days(
    call: Call, active: dict[str, Any], db: Engine, ids: dict[str, int]
) -> None:
    draft_id = call("create_plan", plan=exit_plan(ids))["plan_id"]
    detail = call("get_plan", plan_id=draft_id)
    plan = {**detail, "spec": spec_of(detail)}
    plan["spec"]["days"] = plan["spec"]["days"][1:]
    plan["spec"]["days"][0]["preferred_weekday"] = "mon"
    out = update(call, plan)
    assert out["status"] == "draft"
    assert out["changes"]["days_removed"] == ["Upper"]
    assert out["changes"]["sessions_added"] == []
    with db.connect() as conn:
        assert (
            conn.execute(
                select(func.count()).select_from(plan_days).where(plan_days.c.plan_id == draft_id)
            ).scalar_one()
            == 1
        )
    assert _updates(db) == 0  # drafts are not logged


def test_errors(call: Call, active: dict[str, Any], ids: dict[str, int]) -> None:
    plan = edit(call, active)
    upper = day_of(plan, "Upper")
    plan["spec"]["days"] = [upper, {**day_of(plan, "Lower"), "plan_day_id": upper["plan_day_id"]}]
    with pytest.raises(ToolError, match="listed twice"):
        update(call, plan)

    plan = edit(call, active)
    day_of(plan, "Lower")["plan_day_id"] = 999999
    with pytest.raises(ToolError, match="not a day of plan"):
        update(call, plan)

    plan = edit(call, active)
    day_of(plan, "Upper")["exercises"].append(ex(ids, "pullup", "accessory", 3, 6, 10))
    with pytest.raises(ToolError, match="Not curated yet"):
        update(call, plan)

    plan = edit(call, active)
    call("close_plan", plan_id=active["plan"]["id"], outcome_summary="done")
    with pytest.raises(ToolError, match="only a draft or active plan"):
        update(call, plan)


# Pure schedule rebuild ---------------------------------------------------------


def test_rebuild_schedule_branches() -> None:
    today = date(2026, 10, 5)
    sessions = [
        Session(1, 10, 4, date(2026, 10, 6), "pushed", True),  # changed day, future, pushed
        Session(2, 10, 3, date(2026, 9, 29), "completed", False),  # history
        Session(3, 20, 4, date(2026, 10, 8), "pushed", True),  # removed day
        Session(4, 10, 5, date(2026, 10, 13), "planned", False),  # week dropped
    ]
    days = [DayPlan(0, 10, "wed", moved=True, changed=True), DayPlan(1, None, "sat", False, True)]
    out = rebuild_schedule(START, 4, days, {20}, sessions, today)
    assert sorted(out.delete) == [3, 4]
    assert sorted(out.unpush) == [1, 3]
    assert out.move == [(1, date(2026, 10, 7))]
    assert out.insert == [(1, 4, date(2026, 10, 10))]
