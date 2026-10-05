"""Plan facts the planning tools and the snapshot share: prescriptions, exposures,
RIR feedback and deload weeks. Query helpers only; the rules stay in `spotter.engine`.

Weights leave the database as kg and reach the engine as lb, via `spotter.units` only.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, cast

from sqlalchemy import Connection, Row, select

from spotter.db.schema import (
    activities,
    adjustments,
    exercises,
    performed_sets,
    plan_days,
    plan_exercises,
    prescribed_sets,
    scheduled_sessions,
    session_notes,
)
from spotter.engine.plan_eval import plan_week
from spotter.engine.rules import HISTORY_DAYS, LOWER_BODY_PATTERNS
from spotter.engine.types import Exposure, LoadedSet, LoadType, Model, Prescription, Role
from spotter.garmin.mappers import STRENGTH_TYPE
from spotter.units import kg_to_lb

MODELS: tuple[Model, ...] = ("double_progression", "linear", "rir_based")


def model_for(role: str, plan_model: str, override: Mapping[str, Any] | None) -> Model:
    """`progression_override.model` wins; else the plan model for main lifts; else double
    progression (decisions.md, "Model scope")."""
    if override and override.get("model") in MODELS:
        return cast(Model, override["model"])
    if role == "main":
        return cast(Model, plan_model)
    return "double_progression"


def prescription(pe: Row[Any], ex: Row[Any], plan_model: str) -> Prescription:
    """One plan exercise row and its exercise row, as the rules read them."""
    return Prescription(
        sets=pe.sets,
        rep_min=pe.rep_min,
        rep_max=pe.rep_max,
        target_rir=pe.target_rir,
        rest_s=pe.rest_s,
        role=cast(Role, pe.role),
        model=model_for(pe.role, plan_model, pe.progression_override),
        increment_lb=None if ex.increment_lb is None else Decimal(ex.increment_lb),
        load_type=cast(LoadType | None, ex.load_type),
        lower_body=ex.movement_pattern in LOWER_BODY_PATTERNS,
    )


def plan_exercise_rows(conn: Connection, plan_id: int, plan_day_id: int | None = None) -> list[Any]:
    """Plan exercises with their exercise fields, in day order then position."""
    query = (
        select(
            plan_exercises,
            plan_days.c.day_order,
            exercises.c.display_name,
            exercises.c.increment_lb,
            exercises.c.load_type,
            exercises.c.movement_pattern,
        )
        .join(plan_days, plan_days.c.id == plan_exercises.c.plan_day_id)
        .join(exercises, exercises.c.id == plan_exercises.c.exercise_id)
        .where(plan_days.c.plan_id == plan_id)
        .order_by(plan_days.c.day_order, plan_exercises.c.position)
    )
    if plan_day_id is not None:
        query = query.where(plan_exercises.c.plan_day_id == plan_day_id)
    return list(conn.execute(query))


def plan_prescriptions(conn: Connection, plan: Row[Any]) -> dict[int, tuple[str, Prescription]]:
    """Exercise id -> (name, prescription), first occurrence by day order and position."""
    out: dict[int, tuple[str, Prescription]] = {}
    for r in plan_exercise_rows(conn, plan.id):
        if r.exercise_id not in out:
            out[r.exercise_id] = (r.display_name, prescription(r, r, plan.progression_model))
    return out


def deload_weeks(conn: Connection, plan: Row[Any]) -> set[int]:
    """The planned deload week plus any `record_adjustment(kind="deload")` week."""
    weeks = {plan.deload_week} if plan.deload_week is not None else set()
    for payload in conn.execute(
        select(adjustments.c.payload).where(
            adjustments.c.plan_id == plan.id, adjustments.c.kind == "deload"
        )
    ).scalars():
        if isinstance(payload, dict) and isinstance(payload.get("week_no"), int):
            weeks.add(payload["week_no"])
    return weeks


def is_deload(plan: Row[Any], weeks: set[int], day: date) -> bool:
    week = plan_week(plan.start_date, day)
    return week is not None and week in weeks


def rir_by_date(conn: Connection, exercise_id: int) -> dict[date, int]:
    """RIR feedback for one exercise by session date. A note tied to a scheduled session
    dates by that session's activity (else its scheduled date); others by `local_date`.
    The newest note wins."""
    key = str(exercise_id)
    rows = conn.execute(
        select(
            session_notes.c.local_date,
            session_notes.c.rir_feedback,
            scheduled_sessions.c.scheduled_date,
            activities.c.local_date.label("activity_date"),
        )
        .outerjoin(
            scheduled_sessions, scheduled_sessions.c.id == session_notes.c.scheduled_session_id
        )
        .outerjoin(activities, activities.c.garmin_activity_id == scheduled_sessions.c.activity_id)
        .where(session_notes.c.rir_feedback.has_key(key))
        .order_by(session_notes.c.created_at, session_notes.c.id)
    )
    out: dict[date, int] = {}
    for r in rows:
        day = r.activity_date or r.scheduled_date or r.local_date
        out[day] = int(r.rir_feedback[key])
    return out


def stored_targets(
    conn: Connection, exercise_id: int, activity_ids: list[int]
) -> dict[int, tuple[int, ...]]:
    """Activity id -> target reps of the sets prescribed at the top weight, in set order,
    for activities linked to a pushed session that prescribed this exercise. Progression
    judges sets at the working weight, so back-off targets are left out."""
    rows = conn.execute(
        select(
            scheduled_sessions.c.activity_id,
            prescribed_sets.c.target_weight_kg,
            prescribed_sets.c.target_reps,
        )
        .join(
            prescribed_sets,
            prescribed_sets.c.scheduled_session_id == scheduled_sessions.c.id,
        )
        .where(
            scheduled_sessions.c.activity_id.in_(activity_ids),
            prescribed_sets.c.exercise_id == exercise_id,
        )
        .order_by(scheduled_sessions.c.activity_id, prescribed_sets.c.set_no)
    )
    by_activity: dict[int, list[tuple[Decimal, int]]] = {}
    for r in rows:
        by_activity.setdefault(r.activity_id, []).append(
            (r.target_weight_kg or Decimal(0), r.target_reps)
        )
    out: dict[int, tuple[int, ...]] = {}
    for activity_id, sets in by_activity.items():
        top = max(w for w, _ in sets)
        out[activity_id] = tuple(reps for w, reps in sets if w == top)
    return out


def exposures(
    conn: Connection,
    exercise_id: int,
    before: date,
    plan: Row[Any] | None = None,
    deload: set[int] | None = None,
) -> list[Exposure]:
    """Strength sessions of one exercise in the 12 weeks before `before`, oldest first.

    Sets in set order, in lb. 0-rep sets are dropped (the watch logs them as ACTIVE).
    A session linked to a pushed prescription carries its stored targets.
    Exposures in a deload week of `plan` are flagged so progression skips them.
    """
    rows = conn.execute(
        select(
            performed_sets.c.activity_id,
            performed_sets.c.weight_kg,
            performed_sets.c.reps,
            activities.c.local_date,
        )
        .join(activities, activities.c.garmin_activity_id == performed_sets.c.activity_id)
        .where(
            performed_sets.c.exercise_id == exercise_id,
            activities.c.type == STRENGTH_TYPE,
            activities.c.local_date >= before - timedelta(days=HISTORY_DAYS),
            activities.c.local_date < before,
            performed_sets.c.reps > 0,
        )
        .order_by(activities.c.start_time, performed_sets.c.activity_id, performed_sets.c.set_index)
    )
    sessions: dict[int, tuple[date, list[LoadedSet]]] = {}
    for r in rows:
        weight = None if not r.weight_kg else kg_to_lb(r.weight_kg)
        sessions.setdefault(r.activity_id, (r.local_date, []))[1].append(LoadedSet(weight, r.reps))
    rir = rir_by_date(conn, exercise_id) if sessions else {}
    targets = stored_targets(conn, exercise_id, list(sessions)) if sessions else {}
    weeks = deload or set()
    return [
        Exposure(
            local_date=day,
            sets=tuple(sets),
            rir=rir.get(day),
            targets=targets.get(activity_id),
            deload=plan is not None and is_deload(plan, weeks, day),
        )
        for activity_id, (day, sets) in sessions.items()
    ]
