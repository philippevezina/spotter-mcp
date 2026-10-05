"""Write tools (SPEC 11.3), Phase 4 subset: profile, goals, curation, plans, the
adjustment log and session notes. Nothing here touches Garmin.

Command functions take a Connection and the athlete's local `today`, and raise
ToolError. Callers own the transaction. Weights come in and go out as lb.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field
from sqlalchemy import Connection, Row, exists, func, insert, select, update

from spotter.db.schema import (
    activities,
    adjustments,
    athlete,
    exercises,
    goals,
    performed_sets,
    plan_days,
    plan_exercises,
    plans,
    scheduled_sessions,
    session_notes,
)
from spotter.engine import volume
from spotter.engine.rules import (
    BLOCK_WEEKS_MAX,
    BLOCK_WEEKS_MIN,
    DEFAULT_ADHERENCE_WINDOW_WEEKS,
    DEFAULT_MIN_ADHERENCE,
    DEFAULT_STALL_LIFTS,
    HISTORY_DAYS,
    MUSCLES,
)
from spotter.engine.types import LoadType, Model, Role, VolumeDay, VolumeExercise
from spotter.mcp.deps import Deps
from spotter.mcp.serialize import iso, lb, num
from spotter.mcp.tools.context import OPEN_SESSION, plan_detail
from spotter.mcp.tools.history import MODELS, model_for
from spotter.sync.stats import rebuild_all
from spotter.sync.sync import link_sessions
from spotter.units import lb_to_kg

WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}
IDEMPOTENT = {**WRITE, "idempotentHint": True}

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
WEEKDAYS: tuple[Weekday, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
Pattern = Literal[
    "squat", "hinge", "push_h", "push_v", "pull_h", "pull_v", "lunge", "carry", "core", "isolation"
]
Muscle = Literal[
    "chest",
    "back",
    "shoulders",
    "biceps",
    "triceps",
    "quads",
    "hamstrings",
    "glutes",
    "calves",
    "core",
]
GoalKind = Literal["strength", "hypertrophy", "hybrid", "endurance_support"]
GoalStatus = Literal["active", "achieved", "dropped"]
AdjustmentKind = Literal[
    "load_change", "swap", "deload", "reschedule", "volume_change", "end_plan", "other"
]

# Smallest real jump by load type (SPEC section 6). Bodyweight cannot progress by load.
DEFAULT_INCREMENT_LB: dict[str, Decimal | None] = {
    "barbell": Decimal(5),
    "dumbbell_each": Decimal(5),
    "machine": Decimal(5),
    "cable": Decimal(5),
    "bodyweight_plus": Decimal(5),
    "bodyweight": None,
}
E1RM_LOAD_TYPES = frozenset({"barbell", "dumbbell_each"})
NON_COMPOUND = frozenset({"isolation", "core", "carry"})
BODYWEIGHT_STEP_LB = Decimal("0.1")
GOAL_STEP_LB = Decimal("0.5")
E1RM_UNIT = "e1rm"
START_WINDOW_DAYS = 28  # activate_plan start_date reach, both ways (decisions.md)

assert set(DEFAULT_INCREMENT_LB) == set(LoadType.__args__)  # type: ignore[attr-defined]
assert set(Muscle.__args__) == MUSCLES  # type: ignore[attr-defined]


# Athlete and goals ------------------------------------------------------------


def _athlete_out(a: Row[Any]) -> dict[str, Any]:
    return {
        "bodyweight_lb": lb(a.bodyweight_kg),
        "training_age_years": num(a.training_age_years),
        "available_days": a.available_days,
        "session_minutes": a.session_minutes,
        "equipment": a.equipment,
        "limitations": a.limitations,
        "time_zone": a.time_zone,
    }


def upsert_athlete(conn: Connection, fields: dict[str, Any]) -> dict[str, Any]:
    """Partial update of the single athlete row, created on first use."""
    values = {k: v for k, v in fields.items() if v is not None}
    if "bodyweight_lb" in values:
        values["bodyweight_kg"] = lb_to_kg(values.pop("bodyweight_lb"), BODYWEIGHT_STEP_LB)
    if conn.execute(select(athlete.c.id)).first() is None:
        conn.execute(insert(athlete).values(id=1))
    if values:
        conn.execute(update(athlete).values(**values, updated_at=func.now()))
    return _athlete_out(conn.execute(select(athlete)).one())


def _goal_out(g: Row[Any]) -> dict[str, Any]:
    value: Any = num(g.target_value, 2)
    if g.target_unit == E1RM_UNIT:
        value = lb(g.target_value)
    return {
        "goal_id": g.id,
        "kind": g.kind,
        "description": g.description,
        "target_exercise_id": g.target_exercise_id,
        "target_value": value,
        "target_unit": g.target_unit,
        "target_date": iso(g.target_date),
        "status": g.status,
    }


def _require_exercise(conn: Connection, exercise_id: int) -> Row[Any]:
    ex = conn.execute(select(exercises).where(exercises.c.id == exercise_id)).first()
    if ex is None:
        raise ToolError(f"No exercise with id {exercise_id}. Use search_exercises to find one.")
    return ex


def upsert_goal_row(
    conn: Connection, goal_id: int | None, fields: dict[str, Any]
) -> dict[str, Any]:
    """Create a goal, or update the given fields of `goal_id`. e1RM targets arrive in lb."""
    values = {k: v for k, v in fields.items() if v is not None}
    current = None
    if goal_id is not None:
        current = conn.execute(select(goals).where(goals.c.id == goal_id)).first()
        if current is None:
            raise ToolError(f"No goal with id {goal_id}.")
    if "target_exercise_id" in values:
        _require_exercise(conn, values["target_exercise_id"])
    unit = values.get("target_unit", current.target_unit if current else None)
    if "target_value" in values and unit == E1RM_UNIT:
        values["target_value"] = lb_to_kg(values["target_value"], GOAL_STEP_LB)
    if current is None:
        missing = [k for k in ("kind", "description") if k not in values]
        if missing:
            raise ToolError(f"A new goal needs {' and '.join(missing)}.")
        goal_id = conn.execute(insert(goals).values(**values).returning(goals.c.id)).scalar_one()
    elif values:
        conn.execute(update(goals).where(goals.c.id == goal_id).values(**values))
    return _goal_out(conn.execute(select(goals).where(goals.c.id == goal_id)).one())


# Curation ---------------------------------------------------------------------


def _exercise_out(r: Row[Any]) -> dict[str, Any]:
    return {
        "id": r.id,
        "name": r.display_name,
        "curated": r.curated,
        "movement_pattern": r.movement_pattern,
        "primary_muscles": r.primary_muscles,
        "secondary_muscles": r.secondary_muscles,
        "load_type": r.load_type,
        "increment_lb": num(r.increment_lb, 2),
        "e1rm_eligible": r.e1rm_eligible,
    }


def curate(
    conn: Connection,
    exercise_id: int,
    movement_pattern: str,
    primary_muscles: list[str],
    load_type: str,
    secondary_muscles: list[str] | None = None,
    increment_lb: Decimal | None = None,
    e1rm_eligible: bool | None = None,
) -> dict[str, Any]:
    """Set the curated fields. Rebuilds the exercise's stats when e1RM eligibility changes."""
    ex = _require_exercise(conn, exercise_id)
    secondary = [m for m in dict.fromkeys(secondary_muscles or []) if m not in primary_muscles]
    if increment_lb is None:
        increment_lb = DEFAULT_INCREMENT_LB[load_type]
    if e1rm_eligible is None:
        e1rm_eligible = load_type in E1RM_LOAD_TYPES and movement_pattern not in NON_COMPOUND
    conn.execute(
        update(exercises)
        .where(exercises.c.id == exercise_id)
        .values(
            curated=True,
            movement_pattern=movement_pattern,
            primary_muscles=list(dict.fromkeys(primary_muscles)),
            secondary_muscles=secondary,
            load_type=load_type,
            increment_lb=increment_lb,
            e1rm_eligible=e1rm_eligible,
        )
    )
    rebuilt = rebuild_all(conn, exercise_id) if e1rm_eligible != ex.e1rm_eligible else 0
    row = conn.execute(select(exercises).where(exercises.c.id == exercise_id)).one()
    return {**_exercise_out(row), "activities_rebuilt": rebuilt}


# Plans ------------------------------------------------------------------------


class PlanExerciseIn(BaseModel):
    exercise_id: int
    role: Role = "accessory"
    sets: Annotated[int, Field(ge=1, le=10)]
    rep_min: Annotated[int, Field(ge=1, le=30)]
    rep_max: Annotated[int, Field(ge=1, le=30)]
    target_rir: Annotated[int, Field(ge=0, le=5)]
    rest_s: Annotated[int, Field(ge=0, le=600)]
    superset_group: str | None = None
    progression_override: dict[str, Any] | None = Field(
        default=None, description='e.g. {"model": "linear"}; wins over the plan model.'
    )


class PlanDayIn(BaseModel):
    label: str = Field(min_length=1, description='e.g. "Lower A"')
    preferred_weekday: Weekday
    exercises: list[PlanExerciseIn] = Field(min_length=1)


class EndCriteriaIn(BaseModel):
    max_weeks: Annotated[int, Field(ge=1, le=16)] | None = None
    stall_lifts: Annotated[int, Field(ge=1, le=10)] | None = None
    min_adherence: Annotated[float, Field(ge=0, le=1)] | None = None
    adherence_window_weeks: Annotated[int, Field(ge=1, le=8)] | None = None


class PlanIn(BaseModel):
    name: str = Field(min_length=1)
    rationale: str = Field(min_length=1, description="Why this block, its length and its model.")
    weeks: Annotated[int, Field(ge=1, le=16)]
    progression_model: Model
    days: list[PlanDayIn] = Field(min_length=1, max_length=7)
    deload_week: int | None = None
    goal_id: int | None = None
    end_criteria: EndCriteriaIn | None = None


def _end_criteria(spec: PlanIn) -> dict[str, Any]:
    """Stored in full. `max_weeks` defaults to the plan length, so a block Claude sized at
    8 weeks is not ended at the A.10 default of 6 (decisions.md)."""
    given = spec.end_criteria or EndCriteriaIn()
    return {
        "max_weeks": given.max_weeks if given.max_weeks is not None else spec.weeks,
        "stall_lifts": given.stall_lifts if given.stall_lifts is not None else DEFAULT_STALL_LIFTS,
        "min_adherence": given.min_adherence
        if given.min_adherence is not None
        else float(DEFAULT_MIN_ADHERENCE),
        "adherence_window_weeks": given.adherence_window_weeks
        if given.adherence_window_weeks is not None
        else DEFAULT_ADHERENCE_WINDOW_WEEKS,
    }


def _validate_plan(conn: Connection, spec: PlanIn) -> dict[int, Row[Any]]:
    """Hard errors. Returns the plan's exercise rows by id."""
    errors: list[str] = []
    weekdays = [d.preferred_weekday for d in spec.days]
    dupes = sorted({w for w in weekdays if weekdays.count(w) > 1})
    if dupes:
        errors.append(f"Each day needs its own weekday; repeated: {', '.join(dupes)}.")
    if spec.deload_week is not None and not 1 <= spec.deload_week <= spec.weeks:
        errors.append(f"deload_week must be between 1 and {spec.weeks}.")
    if spec.goal_id is not None and (
        conn.execute(select(goals.c.id).where(goals.c.id == spec.goal_id)).first() is None
    ):
        errors.append(f"No goal with id {spec.goal_id}.")
    for d in spec.days:
        for e in d.exercises:
            if e.rep_min > e.rep_max:
                errors.append(
                    f"{d.label}: exercise {e.exercise_id} has rep_min {e.rep_min} > "
                    f"rep_max {e.rep_max}."
                )
            override = e.progression_override or {}
            if "model" in override and override["model"] not in MODELS:
                errors.append(
                    f"{d.label}: exercise {e.exercise_id} override model must be one of "
                    f"{', '.join(MODELS)}."
                )
    ids = sorted({e.exercise_id for d in spec.days for e in d.exercises})
    rows = {r.id: r for r in conn.execute(select(exercises).where(exercises.c.id.in_(ids)))}
    unknown = [i for i in ids if i not in rows]
    if unknown:
        errors.append(
            f"Unknown exercise ids: {', '.join(map(str, unknown))}. Use search_exercises."
        )
    uncurated = [i for i in ids if i in rows and not rows[i].curated]
    if uncurated:
        names = ", ".join(f"{i} ({rows[i].display_name})" for i in uncurated)
        errors.append(f"Not curated yet: {names}. Call curate_exercise for each first.")
    if errors:
        raise ToolError(" ".join(errors))
    return rows


def _volume_days(spec: PlanIn, rows: dict[int, Row[Any]]) -> list[VolumeDay]:
    return [
        VolumeDay(
            label=d.label,
            exercises=tuple(
                VolumeExercise(
                    sets=e.sets,
                    primary=tuple(rows[e.exercise_id].primary_muscles or ()),
                    secondary=tuple(rows[e.exercise_id].secondary_muscles or ()),
                )
                for e in d.exercises
            ),
        )
        for d in spec.days
    ]


def _plan_warnings(
    conn: Connection, spec: PlanIn, rows: dict[int, Row[Any]], today: date
) -> list[str]:
    since = today - timedelta(days=HISTORY_DAYS)
    recent = conn.execute(
        select(
            exists().where(
                performed_sets.c.exercise_id.in_(list(rows)),
                performed_sets.c.activity_id.in_(
                    select(activities.c.garmin_activity_id).where(activities.c.local_date >= since)
                ),
            )
        )
    ).scalar_one()
    warnings = volume.check(_volume_days(spec, rows), recent_history=bool(recent))
    if not BLOCK_WEEKS_MIN <= spec.weeks <= BLOCK_WEEKS_MAX:
        warnings.append(f"block_weeks:{spec.weeks} outside {BLOCK_WEEKS_MIN}-{BLOCK_WEEKS_MAX}")
    for d in spec.days:
        for e in d.exercises:
            model = model_for(e.role, spec.progression_model, e.progression_override)
            if model == "linear" and e.rep_min != e.rep_max:
                warnings.append(
                    f"linear_rep_range:{rows[e.exercise_id].display_name}:{e.rep_min}-{e.rep_max} "
                    f"(linear targets rep_max on every set)"
                )
    available = conn.execute(select(athlete.c.available_days)).scalar()
    if available:
        outside = [d.preferred_weekday for d in spec.days if d.preferred_weekday not in available]
        if outside:
            warnings.append(f"weekday_unavailable:{','.join(outside)}")
    uses_rir = spec.progression_model == "rir_based" or any(
        (e.progression_override or {}).get("model") == "rir_based"
        for d in spec.days
        for e in d.exercises
    )
    if uses_rir:
        feedback = conn.execute(
            select(
                exists().where(
                    session_notes.c.rir_feedback.is_not(None), session_notes.c.local_date >= since
                )
            )
        ).scalar_one()
        if not feedback:
            warnings.append("rir_based_without_feedback (falls back to double progression)")
    return warnings


def create(conn: Connection, spec: PlanIn, today: date) -> dict[str, Any]:
    rows = _validate_plan(conn, spec)
    plan_id = conn.execute(
        insert(plans)
        .values(
            goal_id=spec.goal_id,
            name=spec.name,
            status="draft",
            weeks=spec.weeks,
            sessions_per_week=len(spec.days),
            deload_week=spec.deload_week,
            progression_model=spec.progression_model,
            end_criteria=_end_criteria(spec),
            rationale=spec.rationale,
        )
        .returning(plans.c.id)
    ).scalar_one()
    for order, d in enumerate(spec.days, start=1):
        day_id = conn.execute(
            insert(plan_days)
            .values(
                plan_id=plan_id,
                label=d.label,
                day_order=order,
                preferred_weekday=d.preferred_weekday,
            )
            .returning(plan_days.c.id)
        ).scalar_one()
        conn.execute(
            insert(plan_exercises),
            [
                {
                    "plan_day_id": day_id,
                    "exercise_id": e.exercise_id,
                    "position": pos,
                    "superset_group": e.superset_group,
                    "role": e.role,
                    "sets": e.sets,
                    "rep_min": e.rep_min,
                    "rep_max": e.rep_max,
                    "target_rir": e.target_rir,
                    "rest_s": e.rest_s,
                    "progression_override": e.progression_override,
                }
                for pos, e in enumerate(d.exercises, start=1)
            ],
        )
    weekly = volume.weekly_sets_per_muscle(_volume_days(spec, rows))
    return {
        "plan_id": plan_id,
        "status": "draft",
        "warnings": _plan_warnings(conn, spec, rows, today),
        "weekly_sets_per_muscle": {m: float(n) for m, n in weekly.items()},
    }


def _get_plan(conn: Connection, plan_id: int) -> Row[Any]:
    plan = conn.execute(select(plans).where(plans.c.id == plan_id)).first()
    if plan is None:
        raise ToolError(f"No plan with id {plan_id}.")
    return plan


def _retire(conn: Connection, plan_id: int, status: str, today: date, **extra: Any) -> int:
    """End a plan and skip every open session of it, past ones included (decisions.md)."""
    conn.execute(
        update(plans)
        .where(plans.c.id == plan_id)
        .values(status=status, actual_end_date=today, **extra)
    )
    day_ids = select(plan_days.c.id).where(plan_days.c.plan_id == plan_id)
    return conn.execute(
        update(scheduled_sessions)
        .where(
            scheduled_sessions.c.plan_day_id.in_(day_ids),
            scheduled_sessions.c.status.in_(OPEN_SESSION),
        )
        .values(status="skipped")
    ).rowcount


def session_date(start: date, week_no: int, weekday: str) -> date:
    """The given weekday inside week `week_no`, which runs 7 days from start + 7(week_no - 1)."""
    week_start = start + timedelta(weeks=week_no - 1)
    return week_start + timedelta(days=(WEEKDAYS.index(weekday) - week_start.weekday()) % 7)


def activate(
    conn: Connection, plan_id: int, start_date: date, replace: bool, today: date
) -> dict[str, Any]:
    plan = _get_plan(conn, plan_id)
    if plan.status == "active" and plan.start_date == start_date:
        return {"linked": 0, "replaced_plan_id": None, **plan_detail(conn, today, plan_id)}
    if plan.status != "draft":
        raise ToolError(
            f"Plan {plan_id} is {plan.status}; only a draft can be activated. "
            "Create a new plan instead."
        )
    earliest = today - timedelta(days=START_WINDOW_DAYS)
    latest = today + timedelta(days=START_WINDOW_DAYS)
    if not earliest <= start_date <= latest:
        raise ToolError(
            f"start_date must be between {earliest.isoformat()} and {latest.isoformat()}."
        )
    active = conn.execute(select(plans).where(plans.c.status == "active")).first()
    if active is not None and not replace:
        raise ToolError(
            f"Plan {active.id} ({active.name}) is active. Pass replace=true to abandon it, "
            "only after the athlete agrees, or close_plan it first."
        )
    if active is not None:
        _retire(conn, active.id, "abandoned", today)

    days = conn.execute(
        select(plan_days).where(plan_days.c.plan_id == plan_id).order_by(plan_days.c.day_order)
    ).all()
    conn.execute(
        insert(scheduled_sessions),
        [
            {
                "plan_day_id": d.id,
                "week_no": week,
                "scheduled_date": session_date(start_date, week, d.preferred_weekday),
            }
            for week in range(1, plan.weeks + 1)
            for d in days
        ],
    )
    conn.execute(
        update(plans)
        .where(plans.c.id == plan_id)
        .values(
            status="active",
            start_date=start_date,
            planned_end_date=start_date + timedelta(weeks=plan.weeks, days=-1),
        )
    )
    linked = link_sessions(conn, start_date, today) if start_date <= today else 0
    return {
        "linked": linked,
        "replaced_plan_id": None if active is None else active.id,
        **plan_detail(conn, today, plan_id),
    }


def close(conn: Connection, plan_id: int, outcome_summary: str, today: date) -> dict[str, Any]:
    plan = _get_plan(conn, plan_id)
    if plan.status == "completed":
        return {
            "plan_id": plan_id,
            "status": "completed",
            "sessions_skipped": 0,
            "actual_end_date": iso(plan.actual_end_date),
        }
    if plan.status != "active":
        raise ToolError(f"Plan {plan_id} is {plan.status}; only an active plan can be closed.")
    skipped = _retire(conn, plan_id, "completed", today, outcome_summary=outcome_summary)
    return {
        "plan_id": plan_id,
        "status": "completed",
        "sessions_skipped": skipped,
        "actual_end_date": iso(today),
    }


# Log --------------------------------------------------------------------------


def adjust(
    conn: Connection,
    plan_id: int | None,
    kind: str,
    reason: str,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    if plan_id is None:
        plan = conn.execute(select(plans).where(plans.c.status == "active")).first()
        if plan is None:
            raise ToolError("No active plan. Pass plan_id.")
    else:
        plan = _get_plan(conn, plan_id)
    if kind == "deload":
        week = (payload or {}).get("week_no")
        if not isinstance(week, int) or not 1 <= week <= plan.weeks:
            raise ToolError(
                f'A deload needs payload {{"week_no": N}} with N from 1 to {plan.weeks}.'
            )
    row = conn.execute(
        insert(adjustments)
        .values(plan_id=plan.id, kind=kind, reason=reason, payload=payload)
        .returning(adjustments)
    ).one()
    return {
        "adjustment_id": row.id,
        "plan_id": row.plan_id,
        "kind": row.kind,
        "reason": row.reason,
        "payload": row.payload,
        "created_at": iso(row.created_at),
    }


def note(
    conn: Connection,
    text: str,
    today: date,
    tags: list[str] | None,
    rir_feedback: dict[int, int] | None,
    scheduled_session_id: int | None,
    local_date: date | None,
) -> dict[str, Any]:
    if scheduled_session_id is not None:
        session = conn.execute(
            select(scheduled_sessions.c.scheduled_date).where(
                scheduled_sessions.c.id == scheduled_session_id
            )
        ).first()
        if session is None:
            raise ToolError(f"No scheduled session with id {scheduled_session_id}.")
        local_date = local_date or session.scheduled_date
    if rir_feedback:
        ids = sorted(rir_feedback)
        known = set(conn.execute(select(exercises.c.id).where(exercises.c.id.in_(ids))).scalars())
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise ToolError(
                f"Unknown exercise ids in rir_feedback: {', '.join(map(str, unknown))}."
            )
    row = conn.execute(
        insert(session_notes)
        .values(
            local_date=local_date or today,
            scheduled_session_id=scheduled_session_id,
            text=text,
            tags=tags,
            rir_feedback=None if not rir_feedback else {str(k): v for k, v in rir_feedback.items()},
        )
        .returning(session_notes)
    ).one()
    return {
        "note_id": row.id,
        "local_date": iso(row.local_date),
        "scheduled_session_id": row.scheduled_session_id,
        "tags": row.tags,
        "rir_feedback": row.rir_feedback,
    }


# Registration -----------------------------------------------------------------


def register(mcp: FastMCP, deps: Deps) -> None:
    @mcp.tool(annotations=IDEMPOTENT)
    def upsert_athlete_profile(
        bodyweight_lb: Annotated[float, Field(gt=0, lt=1000)] | None = None,
        training_age_years: Annotated[float, Field(ge=0, le=80)] | None = None,
        available_days: list[Weekday] | None = None,
        session_minutes: Annotated[int, Field(ge=10, le=300)] | None = None,
        equipment: dict[str, Any] | None = None,
        limitations: str | None = None,
    ) -> dict[str, Any]:
        """Update the athlete profile. Only the fields passed change. Bodyweight in lb.
        available_days use mon..sun. Call when the athlete states or changes these facts."""
        with deps.engine.begin() as conn:
            return upsert_athlete(
                conn,
                {
                    "bodyweight_lb": bodyweight_lb,
                    "training_age_years": training_age_years,
                    "available_days": available_days,
                    "session_minutes": session_minutes,
                    "equipment": equipment,
                    "limitations": limitations,
                },
            )

    @mcp.tool(annotations=IDEMPOTENT)
    def upsert_goal(
        goal_id: int | None = None,
        kind: GoalKind | None = None,
        description: str | None = None,
        target_exercise_id: int | None = None,
        target_value: Annotated[float, Field(gt=0)] | None = None,
        target_unit: str | None = None,
        target_date: date | None = None,
        status: GoalStatus | None = None,
    ) -> dict[str, Any]:
        """Create a goal (kind and description required), or update the fields passed when
        goal_id is given. For an estimated 1RM target use target_unit "e1rm" and target_value
        in lb. Call after the athlete states or confirms the goal."""
        with deps.engine.begin() as conn:
            return upsert_goal_row(
                conn,
                goal_id,
                {
                    "kind": kind,
                    "description": description,
                    "target_exercise_id": target_exercise_id,
                    "target_value": target_value,
                    "target_unit": target_unit,
                    "target_date": target_date,
                    "status": status,
                },
            )

    @mcp.tool(annotations=IDEMPOTENT)
    def curate_exercise(
        exercise_id: int,
        movement_pattern: Pattern,
        primary_muscles: Annotated[list[Muscle], Field(min_length=1)],
        load_type: LoadType,
        secondary_muscles: list[Muscle] | None = None,
        increment_lb: Annotated[float, Field(gt=0, le=50)] | None = None,
        e1rm_eligible: bool | None = None,
    ) -> dict[str, Any]:
        """Fill an exercise's coaching fields. Required before the exercise can go in a plan.
        increment_lb is the smallest real jump (default 5; none for bodyweight; dumbbells are
        per hand). e1rm_eligible defaults to true for barbell and dumbbell compounds. Changing
        it rebuilds that exercise's history stats."""
        with deps.engine.begin() as conn:
            return curate(
                conn,
                exercise_id,
                movement_pattern,
                list(primary_muscles),
                load_type,
                list(secondary_muscles or []),
                None if increment_lb is None else Decimal(str(increment_lb)),
                e1rm_eligible,
            )

    @mcp.tool(annotations=WRITE)
    def create_plan(plan: PlanIn) -> dict[str, Any]:
        """Save a training block as a draft. Each day runs once a week on its own
        preferred_weekday. Every exercise must be curated first. Pick 4 to 8 weeks and explain
        the length and model in rationale. plans.progression_model applies to main lifts;
        secondary and accessory work uses double progression unless progression_override says
        otherwise. Returns warnings (volume, block length, rep ranges); review them with the
        athlete. Call only after the athlete agrees to the plan."""
        with deps.engine.begin() as conn:
            return create(conn, plan, deps.today(conn))

    @mcp.tool(annotations=IDEMPOTENT)
    def activate_plan(plan_id: int, start_date: date, replace: bool = False) -> dict[str, Any]:
        """Activate a draft plan from start_date (up to 28 days back or ahead): schedules one
        session per day per week, and links strength sessions already done since start_date.
        replace=true abandons the current active plan; use it only after the athlete agrees.
        Nothing is sent to Garmin. Call only after the athlete confirms the start date."""
        with deps.engine.begin() as conn:
            return activate(conn, plan_id, start_date, replace, deps.today(conn))

    @mcp.tool(annotations=WRITE)
    def record_adjustment(
        kind: AdjustmentKind,
        reason: str,
        payload: dict[str, Any] | None = None,
        plan_id: int | None = None,
    ) -> dict[str, Any]:
        """Log a change to the plan and why: an override of propose_next_session, a swap, a
        reschedule. A deload needs payload {"week_no": N}; propose_next_session then deloads
        that week. Defaults to the active plan."""
        with deps.engine.begin() as conn:
            return adjust(conn, plan_id, kind, reason, payload)

    @mcp.tool(annotations=WRITE)
    def add_session_note(
        text: str,
        tags: list[str] | None = None,
        rir_feedback: dict[int, Annotated[int, Field(ge=0, le=10)]] | None = None,
        scheduled_session_id: int | None = None,
        local_date: date | None = None,
    ) -> dict[str, Any]:
        """Save a note about a session. rir_feedback maps exercise_id to reps in reserve on
        the working sets, as the athlete reported it; progression reads it. The date defaults
        to the scheduled session's date, else today. Tags e.g. pain, energy, form, time."""
        with deps.engine.begin() as conn:
            return note(
                conn,
                text,
                deps.today(conn),
                tags,
                rir_feedback,
                scheduled_session_id,
                local_date,
            )

    @mcp.tool(annotations=IDEMPOTENT)
    def close_plan(plan_id: int, outcome_summary: str) -> dict[str, Any]:
        """Mark the active plan completed with a short summary of what worked, and skip its
        open sessions. Call when evaluate_plan says end and the athlete agrees, before
        activating the next block."""
        with deps.engine.begin() as conn:
            return close(conn, plan_id, outcome_summary, deps.today(conn))
