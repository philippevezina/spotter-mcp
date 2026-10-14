"""update_plan: edit a draft or active plan in place (decisions.md, "Plan updates").

The spec is the full plan, as create_plan takes it, with `plan_day_id` on each day kept.
History is never touched: on an active plan only open sessions dated after today change.
Pushed sessions whose day changes are removed from Garmin first, outside the transaction,
as close_plan does, so a Garmin failure changes nothing and a retry is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from sqlalchemy import Connection, Row, delete, insert, select, update

from spotter.db.schema import (
    plan_days,
    plan_exercises,
    plans,
    scheduled_sessions,
    session_notes,
)
from spotter.engine import volume
from spotter.mcp.deps import Deps
from spotter.mcp.serialize import iso
from spotter.mcp.tools.context import OPEN_SESSION
from spotter.mcp.tools.garmin_calendar import clear_push, remove_pushed
from spotter.mcp.tools.write import (
    WRITE,
    PlanDayIn,
    PlanExerciseIn,
    PlanIn,
    _end_criteria,
    _get_plan,
    _plan_warnings,
    _validate_plan,
    _volume_days,
    adjust,
    session_date,
)

# Plan fields whose change alters every session's prescription, so every pushed workout.
PRESCRIPTION_FIELDS = ("progression_model", "deload_week")


class PlanDayUpdateIn(PlanDayIn):
    plan_day_id: int | None = Field(
        default=None,
        description="From get_plan, for a day kept. Omit for a new day. A day left out is removed.",
    )


class PlanUpdateIn(PlanIn):
    days: list[PlanDayUpdateIn] = Field(min_length=1, max_length=7)  # type: ignore[assignment]


# Schedule rebuild (pure) ------------------------------------------------------


@dataclass(frozen=True)
class Session:
    id: int
    plan_day_id: int
    week_no: int
    scheduled_date: date
    status: str
    pushed: bool


@dataclass(frozen=True)
class DayPlan:
    """A day of the new spec: its index in the spec, its stored id when kept, its weekday,
    whether the weekday moved, and whether its pushed workouts go stale."""

    index: int
    plan_day_id: int | None
    weekday: str
    moved: bool
    changed: bool


@dataclass
class Schedule:
    insert: list[tuple[int, int, date]] = field(default_factory=list)  # (day index, week, date)
    move: list[tuple[int, date]] = field(default_factory=list)  # (session id, new date)
    delete: list[int] = field(default_factory=list)
    unpush: list[int] = field(default_factory=list)


def rebuild_schedule(
    start: date,
    weeks: int,
    days: list[DayPlan],
    removed_day_ids: set[int],
    sessions: list[Session],
    today: date,
) -> Schedule:
    """What changes in an active plan's schedule. Only open sessions dated after today
    change. A day whose weekday is unchanged keeps its dates (reschedules survive). A
    moved session that would land on or before today keeps its date. New sessions are
    added only after today."""
    out = Schedule()

    def future(s: Session) -> bool:
        return s.status in OPEN_SESSION and s.scheduled_date > today

    for s in sessions:
        if future(s) and (s.plan_day_id in removed_day_ids or s.week_no > weeks):
            out.delete.append(s.id)
            if s.pushed:
                out.unpush.append(s.id)
    by_key = {(s.plan_day_id, s.week_no): s for s in sessions}
    for d in days:
        for week in range(1, weeks + 1):
            target = session_date(start, week, d.weekday)
            old = None if d.plan_day_id is None else by_key.get((d.plan_day_id, week))
            if old is None:
                if target > today:
                    out.insert.append((d.index, week, target))
                continue
            if not future(old):
                continue
            if d.moved and target > today and target != old.scheduled_date:
                out.move.append((old.id, target))
            if old.pushed and d.changed:
                out.unpush.append(old.id)
    return out


# Diff -------------------------------------------------------------------------


@dataclass
class Update:
    plan: Row[Any]
    spec: PlanUpdateIn
    rows: dict[int, Row[Any]]
    stored_days: dict[int, Row[Any]]
    plan_fields: list[str]
    reordered: bool
    added: list[int]  # spec indexes
    changed: list[int]  # spec indexes of kept days whose content changed
    removed: list[int]  # plan_day ids
    sessions: dict[int, Session]
    schedule: Schedule

    @property
    def any(self) -> bool:
        s = self.schedule
        return bool(
            self.plan_fields
            or self.reordered
            or self.added
            or self.changed
            or self.removed
            or s.insert
            or s.move
            or s.delete
            or s.unpush
        )


def _stored_exercises(conn: Connection, day_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {i: [] for i in day_ids}
    for r in conn.execute(
        select(plan_exercises)
        .where(plan_exercises.c.plan_day_id.in_(day_ids))
        .order_by(plan_exercises.c.plan_day_id, plan_exercises.c.position)
    ):
        out[r.plan_day_id].append(
            PlanExerciseIn(
                exercise_id=r.exercise_id,
                role=r.role,
                sets=r.sets,
                rep_min=r.rep_min,
                rep_max=r.rep_max,
                hold_min_s=r.hold_min_s,
                hold_max_s=r.hold_max_s,
                target_rir=r.target_rir,
                rest_s=r.rest_s,
                superset_group=r.superset_group,
                progression_override=r.progression_override,
            ).model_dump()
        )
    return out


def _plan_values(spec: PlanIn) -> dict[str, Any]:
    return {
        "name": spec.name,
        "rationale": spec.rationale,
        "weeks": spec.weeks,
        "progression_model": spec.progression_model,
        "deload_week": spec.deload_week,
        "goal_id": spec.goal_id,
        "end_criteria": _end_criteria(spec),
    }


def _plan_week(start: date, today: date) -> int:
    return 0 if today < start else (today - start).days // 7 + 1


def diff(conn: Connection, plan_id: int, spec: PlanUpdateIn, today: date) -> Update:
    plan = _get_plan(conn, plan_id)
    if plan.status not in ("draft", "active"):
        raise ToolError(
            f"Plan {plan_id} is {plan.status}; only a draft or active plan can be updated."
        )
    stored = {
        d.id: d
        for d in conn.execute(
            select(plan_days)
            .where(plan_days.c.plan_id == plan_id, plan_days.c.removed_on.is_(None))
            .order_by(plan_days.c.day_order)
        )
    }
    given = [d.plan_day_id for d in spec.days if d.plan_day_id is not None]
    errors = []
    unknown = sorted({i for i in given if i not in stored})
    if unknown:
        errors.append(
            f"plan_day_id {', '.join(map(str, unknown))} is not a day of plan {plan_id}; "
            "use get_plan."
        )
    repeated = sorted({i for i in given if given.count(i) > 1})
    if repeated:
        errors.append(f"plan_day_id {', '.join(map(str, repeated))} is listed twice.")
    if plan.status == "active" and spec.weeks < _plan_week(plan.start_date, today):
        errors.append(
            f"weeks must be at least {_plan_week(plan.start_date, today)}, the current week."
        )
    if errors:
        raise ToolError(" ".join(errors))
    rows = _validate_plan(conn, spec)

    values = _plan_values(spec)
    plan_fields = [k for k, v in values.items() if getattr(plan, k) != v]
    exercises_by_day = _stored_exercises(conn, list(stored))
    reordered = [i for i in given] != [i for i in stored if i in given]
    prescription_wide = any(f in plan_fields for f in PRESCRIPTION_FIELDS)
    added, changed, day_plans = [], [], []
    for index, d in enumerate(spec.days):
        old = None if d.plan_day_id is None else stored[d.plan_day_id]
        if old is None:
            added.append(index)
            day_plans.append(DayPlan(index, None, d.preferred_weekday, False, True))
            continue
        moved = old.preferred_weekday != d.preferred_weekday
        content = (
            moved
            or old.label != d.label
            or exercises_by_day[old.id] != [e.model_dump() for e in d.exercises]
        )
        if content:
            changed.append(index)
        day_plans.append(
            DayPlan(index, old.id, d.preferred_weekday, moved, content or prescription_wide)
        )
    removed = [i for i in stored if i not in given]

    sessions: dict[int, Session] = {}
    schedule = Schedule()
    if plan.status == "active":
        sessions = {
            s.id: Session(
                s.id,
                s.plan_day_id,
                s.week_no,
                s.scheduled_date,
                s.status,
                s.garmin_workout_id is not None,
            )
            for s in conn.execute(
                select(scheduled_sessions)
                .join(plan_days, plan_days.c.id == scheduled_sessions.c.plan_day_id)
                .where(plan_days.c.plan_id == plan_id)
                .order_by(scheduled_sessions.c.scheduled_date, plan_days.c.day_order)
            )
        }
        schedule = rebuild_schedule(
            plan.start_date, spec.weeks, day_plans, set(removed), list(sessions.values()), today
        )
    return Update(
        plan,
        spec,
        rows,
        stored,
        plan_fields,
        reordered,
        added,
        changed,
        removed,
        sessions,
        schedule,
    )


def _labels(u: Update) -> dict[int, str]:
    """Day label by plan_day id: the new label for kept days, the stored one for removed."""
    out = {i: d.label for i, d in u.stored_days.items()}
    out.update({d.plan_day_id: d.label for d in u.spec.days if d.plan_day_id is not None})
    return out


def summary(u: Update) -> dict[str, Any]:
    labels = _labels(u)
    s = u.schedule

    def ref(sid: int) -> dict[str, Any]:
        x = u.sessions[sid]
        return {
            "scheduled_session_id": sid,
            "label": labels[x.plan_day_id],
            "week_no": x.week_no,
            "date": iso(x.scheduled_date),
        }

    return {
        "plan": u.plan_fields + (["day_order"] if u.reordered else []),
        "days_added": [u.spec.days[i].label for i in u.added],
        "days_changed": [u.spec.days[i].label for i in u.changed],
        "days_removed": [u.stored_days[i].label for i in u.removed],
        "sessions_added": [
            {"label": u.spec.days[i].label, "week_no": w, "date": iso(d)} for i, w, d in s.insert
        ],
        "sessions_moved": [{**ref(sid), "to": iso(d)} for sid, d in s.move],
        "sessions_removed": [ref(sid) for sid in s.delete],
        "removed_from_garmin": sorted(s.unpush),
    }


# Apply ------------------------------------------------------------------------


def _insert_exercises(conn: Connection, day_id: int, day: PlanDayIn) -> None:
    conn.execute(
        insert(plan_exercises),
        [
            {"plan_day_id": day_id, "position": pos, **e.model_dump()}
            for pos, e in enumerate(day.exercises, start=1)
        ],
    )


def apply(conn: Connection, u: Update, reason: str, today: date) -> None:
    spec, plan, s = u.spec, u.plan, u.schedule
    values = _plan_values(spec) | {"sessions_per_week": len(spec.days)}
    if plan.status == "active":
        values["planned_end_date"] = plan.start_date + timedelta(weeks=spec.weeks, days=-1)
    conn.execute(update(plans).where(plans.c.id == plan.id).values(**values))

    # Sessions leave first, so a removed day without history can be deleted.
    clear_push(conn, s.unpush)
    if s.unpush:
        conn.execute(
            update(scheduled_sessions)
            .where(scheduled_sessions.c.id.in_(s.unpush))
            .values(status="planned")
        )
    if s.delete:
        conn.execute(
            update(session_notes)
            .where(session_notes.c.scheduled_session_id.in_(s.delete))
            .values(scheduled_session_id=None)
        )
        conn.execute(delete(scheduled_sessions).where(scheduled_sessions.c.id.in_(s.delete)))
    for sid, new_date in s.move:
        conn.execute(
            update(scheduled_sessions)
            .where(scheduled_sessions.c.id == sid)
            .values(scheduled_date=new_date)
        )

    # Day order: live days 1..n in spec order, removed days after. Negate first to stay
    # clear of the (plan_id, day_order) unique constraint.
    conn.execute(
        update(plan_days)
        .where(plan_days.c.plan_id == plan.id)
        .values(day_order=-plan_days.c.day_order)
    )
    kept_history = set(
        conn.execute(
            select(scheduled_sessions.c.plan_day_id).where(
                scheduled_sessions.c.plan_day_id.in_(u.removed)
            )
        ).scalars()
    )
    conn.execute(update(plan_days).where(plan_days.c.id.in_(kept_history)).values(removed_on=today))
    conn.execute(
        delete(plan_days).where(plan_days.c.id.in_([i for i in u.removed if i not in kept_history]))
    )

    day_ids: dict[int, int] = {}
    for order, d in enumerate(spec.days, start=1):
        row = {"label": d.label, "day_order": order, "preferred_weekday": d.preferred_weekday}
        if d.plan_day_id is None:
            day_ids[order - 1] = conn.execute(
                insert(plan_days).values(plan_id=plan.id, **row).returning(plan_days.c.id)
            ).scalar_one()
            _insert_exercises(conn, day_ids[order - 1], d)
            continue
        day_ids[order - 1] = d.plan_day_id
        conn.execute(update(plan_days).where(plan_days.c.id == d.plan_day_id).values(**row))
        if order - 1 in u.changed:
            conn.execute(
                delete(plan_exercises).where(plan_exercises.c.plan_day_id == d.plan_day_id)
            )
            _insert_exercises(conn, d.plan_day_id, d)
    retired = conn.execute(
        select(plan_days.c.id)
        .where(plan_days.c.plan_id == plan.id, plan_days.c.removed_on.is_not(None))
        .order_by(plan_days.c.day_order.desc())  # negated: earliest first
    ).scalars()
    for order, day_id in enumerate(retired, start=len(spec.days) + 1):
        conn.execute(update(plan_days).where(plan_days.c.id == day_id).values(day_order=order))

    if s.insert:
        conn.execute(
            insert(scheduled_sessions),
            [
                {"plan_day_id": day_ids[i], "week_no": w, "scheduled_date": d}
                for i, w, d in s.insert
            ],
        )
    if plan.status == "active":
        adjust(conn, plan.id, "plan_update", reason, summary(u))


def result(conn: Connection, u: Update, today: date, dry_run: bool) -> dict[str, Any]:
    weekly = volume.weekly_sets_per_muscle(_volume_days(u.spec, u.rows))
    return {
        "plan_id": u.plan.id,
        "status": u.plan.status,
        "dry_run": dry_run,
        "changed": u.any,
        "changes": summary(u),
        "warnings": _plan_warnings(conn, u.spec, u.rows, today),
        "weekly_sets_per_muscle": {m: float(n) for m, n in weekly.items()},
    }


# Registration -----------------------------------------------------------------


def register(mcp: FastMCP, deps: Deps) -> None:
    @mcp.tool(
        annotations={
            **WRITE,
            "destructiveHint": True,
            "openWorldHint": True,
            "idempotentHint": True,
        }
    )
    def update_plan(
        plan_id: int,
        plan: PlanUpdateIn,
        reason: Annotated[str, Field(min_length=1)],
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Change a draft or active plan in place instead of recreating it. Send the whole
        plan as create_plan takes it, built from get_plan: keep each day's plan_day_id, omit
        it for a new day, leave a day out to remove it. Same validation and warnings as
        create_plan. On an active plan, completed sessions and anything dated today or
        earlier never change; later open sessions move, appear or go with the new weekdays,
        days and weeks, and pushed ones whose day changed are removed from Garmin (push them
        again later). weeks cannot drop below the current week. Use dry_run=true first and
        show the changes. Call only after the athlete agrees."""
        with deps.engine.connect() as conn:
            today = deps.today(conn)
            u = diff(conn, plan_id, plan, today)
            if dry_run or not u.any:
                return result(conn, u, today, dry_run)
            pushed: list[Row[Any]] = list(
                conn.execute(
                    select(
                        scheduled_sessions.c.id,
                        scheduled_sessions.c.garmin_workout_id,
                        scheduled_sessions.c.garmin_schedule_id,
                    ).where(scheduled_sessions.c.id.in_(u.schedule.unpush))
                )
            )
        remove_pushed(deps, pushed)
        with deps.engine.begin() as conn:
            apply(conn, u, reason, today)
            return result(conn, u, today, dry_run)
