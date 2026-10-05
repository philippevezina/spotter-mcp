"""Garmin write tools (SPEC 11.3): push_session and unschedule_session.

A push saves `prescribed_sets`, uploads or updates one Garmin workout per session and
schedules it once. Repeat pushes update the same workout, never duplicate it. Weights
come in and go out as lb; the workout builder converts them for Garmin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Row, delete, insert, select, update

from spotter.db.schema import (
    adjustments,
    exercises,
    plan_days,
    plans,
    prescribed_sets,
    scheduled_sessions,
)
from spotter.engine.rules import BARBELL_MIN_LB
from spotter.garmin.errors import GarminError, GarminNotFound
from spotter.garmin.workouts import (
    Workout,
    WorkoutExercise,
    WorkoutSet,
    build_workout,
    workout_name,
)
from spotter.mcp.deps import Deps
from spotter.mcp.serialize import iso
from spotter.mcp.tools import garmin_sync
from spotter.mcp.tools.calculation import propose
from spotter.mcp.tools.context import OPEN_SESSION
from spotter.mcp.tools.garmin_calendar import clear_push, remove_pushed
from spotter.mcp.tools.write import WRITE, adjust
from spotter.units import lb_to_kg, round_lb

# Load types that cannot be pushed without a weight. Bodyweight has none, and a
# bodyweight_plus set with no weight means no added load.
NEEDS_WEIGHT = frozenset({"barbell", "dumbbell_each", "machine", "cable"})
MANUAL_RULE = "manual"
OFF_PLAN_RULE = "off_plan"


class PushSetIn(BaseModel):
    reps: Annotated[int, Field(ge=1, le=30)]
    weight_lb: Annotated[float, Field(gt=0, lt=1000)] | None = Field(
        default=None, description="Dumbbells per hand. null for bodyweight."
    )


class PushExerciseIn(BaseModel):
    exercise_id: int
    sets: list[PushSetIn] = Field(min_length=1, max_length=10)
    rest_s: Annotated[int, Field(ge=0, le=600)] | None = Field(
        default=None, description="Defaults to the proposal. Required for off-plan exercises."
    )
    target_rir: Annotated[int, Field(ge=0, le=5)] | None = None


@dataclass(frozen=True)
class _Planned:
    """One exercise as it will be pushed."""

    exercise_id: int
    name: str
    ex: Row[Any]
    sets: tuple[WorkoutSet, ...]
    rest_s: int | None
    target_rir: int | None
    rule_fired: str
    last_weight_lb: Decimal | None = None


def _dec(value: float | None) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _lb_text(value: Decimal) -> str:
    return f"{value.normalize():f}"


def _session(conn: Connection, scheduled_session_id: int) -> Row[Any]:
    s = conn.execute(
        select(
            scheduled_sessions,
            plan_days.c.label,
            plan_days.c.plan_id,
            plans.c.status.label("plan_status"),
            plans.c.planned_end_date,
        )
        .join(plan_days, plan_days.c.id == scheduled_sessions.c.plan_day_id)
        .join(plans, plans.c.id == plan_days.c.plan_id)
        .where(scheduled_sessions.c.id == scheduled_session_id)
    ).first()
    if s is None:
        raise ToolError(f"No scheduled session with id {scheduled_session_id}. See get_plan.")
    return s


def _session_out(s: Row[Any]) -> dict[str, Any]:
    return {
        "scheduled_session_id": s.id,
        "plan_id": s.plan_id,
        "label": s.label,
        "date": iso(s.scheduled_date),
        "week_no": s.week_no,
        "status": s.status,
    }


def _check_pushable(s: Row[Any], today: date) -> None:
    if s.plan_status != "active":
        raise ToolError(
            f"Session {s.id} belongs to a {s.plan_status} plan; only the active plan is pushed."
        )
    if s.status not in OPEN_SESSION:
        raise ToolError(
            f"Session {s.id} is {s.status}; only planned or pushed sessions can be pushed."
        )
    if s.scheduled_date < today:
        raise ToolError(
            f"Session {s.id} was due {iso(s.scheduled_date)}. Move it with unschedule_session "
            "and new_date first."
        )


def _exercise_rows(conn: Connection, ids: list[int]) -> dict[int, Row[Any]]:
    return {r.id: r for r in conn.execute(select(exercises).where(exercises.c.id.in_(ids)))}


def _from_proposal(proposal: list[dict[str, Any]], rows: dict[int, Row[Any]]) -> list[_Planned]:
    uncalibrated = [
        p["name"]
        for p in proposal
        if p["weight_lb"] is None and rows[p["exercise_id"]].load_type in NEEDS_WEIGHT
    ]
    if uncalibrated:
        raise ToolError(
            f"No weight for {', '.join(uncalibrated)} (needs_calibration). Ask the athlete for "
            "a recent working weight, then pass exercises with weights."
        )
    return [
        _Planned(
            exercise_id=p["exercise_id"],
            name=p["name"],
            ex=rows[p["exercise_id"]],
            sets=tuple(WorkoutSet(r, _dec(p["weight_lb"])) for r in p["target_reps"]),
            rest_s=p["rest_s"],
            target_rir=p["target_rir"],
            rule_fired=p["rule_fired"],
            last_weight_lb=_dec((p["last"] or {}).get("weight_lb")),
        )
        for p in proposal
    ]


def _from_input(
    given: list[PushExerciseIn],
    proposal: list[dict[str, Any]],
    rows: dict[int, Row[Any]],
) -> tuple[list[_Planned], list[dict[str, Any]]]:
    """Validate and round the athlete's sets. Returns them and the rounding report."""
    errors: list[str] = []
    ids = [e.exercise_id for e in given]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        errors.append(f"List each exercise once; repeated: {', '.join(map(str, dupes))}.")
    unknown = [i for i in dict.fromkeys(ids) if i not in rows]
    if unknown:
        errors.append(
            f"Unknown exercise ids: {', '.join(map(str, unknown))}. Use search_exercises."
        )
    uncurated = [rows[i] for i in dict.fromkeys(ids) if i in rows and not rows[i].curated]
    if uncurated:
        names = ", ".join(f"{r.id} ({r.display_name})" for r in uncurated)
        errors.append(f"Not curated yet: {names}. Call curate_exercise first.")
    if errors:
        raise ToolError(" ".join(errors))

    by_id = {p["exercise_id"]: p for p in proposal}
    planned: list[_Planned] = []
    rounded: list[dict[str, Any]] = []
    for e in given:
        ex = rows[e.exercise_id]
        p = by_id.get(e.exercise_id)
        inc = None if ex.increment_lb is None else Decimal(ex.increment_lb)
        sets: list[WorkoutSet] = []
        for s in e.sets:
            weight = _dec(s.weight_lb)
            if weight is None:
                if ex.load_type in NEEDS_WEIGHT:
                    errors.append(f"{ex.display_name} needs a weight on every set.")
                    break
                sets.append(WorkoutSet(s.reps, None))
                continue
            if inc is None:
                errors.append(f"{ex.display_name} has no load increment; pass weight_lb null.")
                break
            pushed = round_lb(weight, inc)
            if ex.load_type == "barbell" and pushed < BARBELL_MIN_LB:
                pushed = BARBELL_MIN_LB
            if pushed != weight:
                rounded.append(
                    {"exercise_id": ex.id, "given_lb": float(weight), "pushed_lb": float(pushed)}
                )
            sets.append(WorkoutSet(s.reps, pushed))
        if p is None and e.rest_s is None:
            errors.append(f"{ex.display_name} is not on this plan day; pass its rest_s.")
        if p is None:
            rule = OFF_PLAN_RULE
        else:
            proposed = tuple(WorkoutSet(r, _dec(p["weight_lb"])) for r in p["target_reps"])
            rule = p["rule_fired"] if tuple(sets) == proposed else MANUAL_RULE
        planned.append(
            _Planned(
                exercise_id=ex.id,
                name=ex.display_name,
                ex=ex,
                sets=tuple(sets),
                rest_s=e.rest_s if e.rest_s is not None else (p["rest_s"] if p else None),
                target_rir=e.target_rir
                if e.target_rir is not None
                else (p["target_rir"] if p else None),
                rule_fired=rule,
                last_weight_lb=None if p is None else _dec((p["last"] or {}).get("weight_lb")),
            )
        )
    if errors:
        raise ToolError(" ".join(errors))
    return planned, rounded


def _description(planned: list[_Planned]) -> str:
    """The weight changes since the last session, e.g. "Squat +10 lb. Bench +5 lb." """
    notes = []
    for p in planned:
        weight = p.sets[0].weight_lb
        if weight is None or p.last_weight_lb is None or weight == p.last_weight_lb:
            continue
        delta = weight - p.last_weight_lb
        sign = "+" if delta > 0 else "-"
        notes.append(f"{p.name} {sign}{_lb_text(abs(delta))} lb.")
    return " ".join(notes)


def _workout(s: Row[Any], planned: list[_Planned]) -> dict[str, Any]:
    return build_workout(
        Workout(
            name=workout_name(s.label, s.week_no, s.scheduled_date),
            description=_description(planned),
            exercises=tuple(
                WorkoutExercise(
                    garmin_category=p.ex.garmin_category,
                    garmin_name=p.ex.garmin_name,
                    sets=p.sets,
                    rest_s=p.rest_s,
                    increment_lb=None if p.ex.increment_lb is None else Decimal(p.ex.increment_lb),
                )
                for p in planned
            ),
        )
    )


def _steps(payload: dict[str, Any]) -> int:
    """Executable steps the watch walks through, counting each repeat once per set."""
    total = 0
    for step in payload["workoutSegments"][0]["workoutSteps"]:
        if step["type"] == "RepeatGroupDTO":
            total += step["numberOfIterations"] * len(step["workoutSteps"])
        else:
            total += 1
    return total


def _planned_out(p: _Planned) -> dict[str, Any]:
    return {
        "exercise_id": p.exercise_id,
        "name": p.name,
        "sets": [
            {"reps": s.reps, "weight_lb": None if s.weight_lb is None else float(s.weight_lb)}
            for s in p.sets
        ],
        "rest_s": p.rest_s,
        "target_rir": p.target_rir,
        "rule_fired": p.rule_fired,
    }


def _save_workout_id(deps: Deps, session_id: int, workout_id: int) -> None:
    """Saved at once in its own transaction, so a failure later never orphans the upload."""
    with deps.engine.begin() as conn:
        conn.execute(
            update(scheduled_sessions)
            .where(scheduled_sessions.c.id == session_id)
            .values(garmin_workout_id=workout_id, garmin_schedule_id=None)
        )


def _send(deps: Deps, s: Row[Any], payload: dict[str, Any]) -> tuple[int, int, str]:
    """Upload or update the session's workout and make sure it is scheduled once."""
    workout_id, schedule_id, action = s.garmin_workout_id, s.garmin_schedule_id, "updated"
    with deps.garmin(deps.engine) as session:
        client = session.client
        if workout_id is not None:
            try:
                client.update_workout(workout_id, payload)
            except GarminNotFound:  # deleted in Garmin Connect: start over
                if schedule_id is not None:
                    client.unschedule_workout(schedule_id)
                workout_id = schedule_id = None
        if workout_id is None:
            workout_id = client.upload_workout(payload)
            action = "created"
            _save_workout_id(deps, s.id, workout_id)
        if schedule_id is None:
            schedule_id = client.schedule_workout(workout_id, s.scheduled_date)
    return workout_id, schedule_id, action


def _save_push(
    conn: Connection,
    s: Row[Any],
    planned: list[_Planned],
    off_plan: list[_Planned],
    workout_id: int,
    schedule_id: int,
) -> None:
    conn.execute(delete(prescribed_sets).where(prescribed_sets.c.scheduled_session_id == s.id))
    conn.execute(
        insert(prescribed_sets),
        [
            {
                "scheduled_session_id": s.id,
                "exercise_id": p.exercise_id,
                "set_no": n,
                "target_reps": w.reps,
                "target_weight_kg": None
                if w.weight_lb is None
                else lb_to_kg(w.weight_lb, Decimal(p.ex.increment_lb)),
                "target_rir": p.target_rir,
                "rest_s": p.rest_s,
                "rule_fired": p.rule_fired,
            }
            for p in planned
            for n, w in enumerate(p.sets, start=1)
        ],
    )
    conn.execute(
        update(scheduled_sessions)
        .where(scheduled_sessions.c.id == s.id)
        .values(garmin_workout_id=workout_id, garmin_schedule_id=schedule_id, status="pushed")
    )
    for p in off_plan:
        payload = {"scheduled_session_id": s.id, "exercise_id": p.exercise_id}
        logged = conn.execute(
            select(adjustments.c.id).where(
                adjustments.c.plan_id == s.plan_id,
                adjustments.c.kind == "swap",
                adjustments.c.payload.contains(payload),
            )
        ).first()
        if logged is None:
            adjust(
                conn,
                s.plan_id,
                "swap",
                f"Off-plan exercise pushed in {s.label} W{s.week_no}: {p.name}.",
                payload,
            )


def push(
    deps: Deps,
    scheduled_session_id: int,
    given: list[PushExerciseIn] | None,
    dry_run: bool,
) -> dict[str, Any]:
    with deps.engine.connect() as conn:
        today = deps.today(conn)
        s = _session(conn, scheduled_session_id)
        _check_pushable(s, today)
        proposal = propose(conn, s.id, today)["exercises"]
        ids = [p["exercise_id"] for p in proposal] + [e.exercise_id for e in given or []]
        rows = _exercise_rows(conn, ids)
    rounded: list[dict[str, Any]] = []
    if given is None:
        planned = _from_proposal(proposal, rows)
    else:
        planned, rounded = _from_input(given, proposal, rows)
    on_plan = {p["exercise_id"] for p in proposal}
    off_plan = [p for p in planned if p.exercise_id not in on_plan]
    pushed_ids = {p.exercise_id for p in planned}
    payload = _workout(s, planned)
    out: dict[str, Any] = {
        "session": _session_out(s),
        "exercises": [_planned_out(p) for p in planned],
        "steps": _steps(payload),
        "workout_name": payload["workoutName"],
        "description": payload["description"],
        "rounded": rounded,
        "off_plan": [{"exercise_id": p.exercise_id, "name": p.name} for p in off_plan],
        "not_pushed": [
            {"exercise_id": p["exercise_id"], "name": p["name"]}
            for p in proposal
            if p["exercise_id"] not in pushed_ids
        ],
        "dry_run": dry_run,
    }
    if dry_run:
        out["note"] = "Nothing sent or saved. Weights in lb; dumbbells per hand."
        return out
    try:
        workout_id, schedule_id, action = _send(deps, s, payload)
    except GarminError as exc:
        raise ToolError(f"{exc} The prescription was not saved; it is safe to retry.") from exc
    with deps.engine.begin() as conn:
        _save_push(conn, s, planned, off_plan, workout_id, schedule_id)
        s = _session(conn, s.id)
    return {
        **out,
        "session": _session_out(s),
        "action": action,
        "garmin_workout_id": workout_id,
        "garmin_schedule_id": schedule_id,
    }


def unschedule(
    deps: Deps,
    scheduled_session_id: int,
    reason: str,
    new_date: date | None,
) -> dict[str, Any]:
    with deps.engine.connect() as conn:
        today = deps.today(conn)
        s = _session(conn, scheduled_session_id)
    if s.status not in OPEN_SESSION:
        raise ToolError(
            f"Session {s.id} is {s.status}; only planned or pushed sessions can be unscheduled."
        )
    if new_date is not None and not today <= new_date <= s.planned_end_date:
        raise ToolError(
            f"new_date must be between {iso(today)} and the plan end {iso(s.planned_end_date)}."
        )
    if s.garmin_workout_id is not None and s.scheduled_date <= today:
        # It may have been trained but not synced: never delete a trained workout.
        try:
            garmin_sync(deps, force=True)
        except GarminError as exc:
            raise ToolError(f"{exc} Nothing was removed.") from exc
        with deps.engine.connect() as conn:
            s = _session(conn, scheduled_session_id)
        if s.status not in OPEN_SESSION:
            raise ToolError(
                f"Session {s.id} was trained and synced (activity {s.activity_id}); "
                "nothing was removed."
            )
    removed = s.garmin_workout_id is not None
    remove_pushed(deps, [s] if removed else [])
    with deps.engine.begin() as conn:
        clear_push(conn, [s.id])
        if new_date is None:
            values: dict[str, Any] = {"status": "skipped"}
            kind, payload = "other", {"scheduled_session_id": s.id, "from": iso(s.scheduled_date)}
        else:
            values = {"status": "planned", "scheduled_date": new_date}
            kind = "reschedule"
            payload = {
                "scheduled_session_id": s.id,
                "from": iso(s.scheduled_date),
                "to": iso(new_date),
            }
        conn.execute(
            update(scheduled_sessions).where(scheduled_sessions.c.id == s.id).values(**values)
        )
        adjust(conn, s.plan_id, kind, reason, payload)
        s = _session(conn, s.id)
    return {"session": _session_out(s), "removed_from_garmin": removed}


def register(mcp: FastMCP, deps: Deps) -> None:
    @mcp.tool(annotations={**WRITE, "openWorldHint": True, "idempotentHint": True})
    def push_session(
        scheduled_session_id: int,
        exercises: list[PushExerciseIn] | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Send a session to Garmin Connect as a scheduled strength workout and save its
        prescription. Call only after the athlete confirms the session in this conversation.
        Without exercises, pushes the current propose_next_session output. Pass exercises
        (sets in lb, dumbbells per hand) only for sets that differ from the proposal, and
        use dry_run=true first to show the athlete the result. Weights round to the
        exercise increment; changes are listed in rounded. Exercises not on the plan day
        are allowed, returned in off_plan and logged as a swap. Pushing again updates the
        same workout; it never duplicates. The session must be today or later."""
        return push(deps, scheduled_session_id, exercises, dry_run)

    @mcp.tool(annotations={**WRITE, "destructiveHint": True, "openWorldHint": True})
    def unschedule_session(
        scheduled_session_id: int,
        reason: str,
        new_date: date | None = None,
    ) -> dict[str, Any]:
        """Take a planned or pushed session off the plan: removes its workout from the
        Garmin calendar and library and clears its prescription. Without new_date the
        session is skipped. With new_date (today up to the plan end) it moves there and
        returns to planned, ready to push again. A pushed session dated today or earlier is
        synced first; if it was trained, nothing is removed. Logs the reason. Call only
        after the athlete agrees."""
        return unschedule(deps, scheduled_session_id, reason, new_date)
