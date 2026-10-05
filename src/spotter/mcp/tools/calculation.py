"""Calculation tools (SPEC 11.2): next-session proposal and plan evaluation. Read-only.

The rules live in `spotter.engine`; this module loads the facts they read.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from sqlalchemy import Connection, Row, select

from spotter.db.schema import (
    exercise_session_stats,
    goals,
    plan_days,
    plans,
    scheduled_sessions,
)
from spotter.engine import endurance, plan_eval, progression
from spotter.engine.types import ExerciseProposal, Exposure, SessionFact
from spotter.mcp.deps import Deps
from spotter.mcp.serialize import iso, lb, num
from spotter.mcp.tools import READ_ONLY
from spotter.mcp.tools.context import (
    HARD_LOOKBACK_DAYS,
    OPEN_SESSION,
    _active_plan,
    _endurance_rows,
    _is_lower_body_day,
    _readiness_days,
)
from spotter.mcp.tools.history import (
    deload_weeks,
    exposures,
    is_deload,
    plan_exercise_rows,
    plan_prescriptions,
    prescription,
)
from spotter.mcp.tools.write import E1RM_UNIT


def _num_lb(value: Any) -> float | None:
    return None if value is None else float(value)


def _proposal_out(r: Row[Any], model: str, p: ExerciseProposal) -> dict[str, Any]:
    return {
        "exercise_id": r.exercise_id,
        "name": r.display_name,
        "role": r.role,
        "model": model,
        "status": p.status,
        "sets": p.sets,
        "target_reps": list(p.target_reps),
        "weight_lb": _num_lb(p.weight_lb),
        "target_rir": p.target_rir,
        "rest_s": p.rest_s,
        "rule_fired": p.rule_fired,
        "reasons": list(p.reasons),
        "flags": list(p.flags),
        "modifiers": list(p.modifiers),
        "last": None
        if not p.last_reps
        else {"weight_lb": _num_lb(p.last_weight_lb), "reps": list(p.last_reps)},
    }


def propose(conn: Connection, scheduled_session_id: int, today: date) -> dict[str, Any]:
    session = conn.execute(
        select(scheduled_sessions, plan_days.c.label, plan_days.c.plan_id)
        .join(plan_days, plan_days.c.id == scheduled_sessions.c.plan_day_id)
        .where(scheduled_sessions.c.id == scheduled_session_id)
    ).first()
    if session is None:
        raise ToolError(f"No scheduled session with id {scheduled_session_id}. See get_plan.")
    if session.status not in OPEN_SESSION:
        raise ToolError(
            f"Session {scheduled_session_id} is {session.status}; only planned or pushed "
            "sessions get a proposal."
        )
    plan = conn.execute(select(plans).where(plans.c.id == session.plan_id)).one()
    day = session.scheduled_date
    deloads = deload_weeks(conn, plan)
    deload_week = is_deload(plan, deloads, day)

    # A.6 applies on the day only: readiness for any other date is unknown.
    readiness_out: Any = "not_applicable"
    readiness_class = None
    suggest_move = False
    if day == today:
        (_, yesterday), (_, now) = _readiness_days(conn, today - timedelta(days=1), today)
        readiness_class = now.readiness_class if now.has_data else None
        readiness_out = {
            "class": now.readiness_class if now.has_data else "no_data",
            "reasons": list(now.reasons),
        }
        suggest_move = (
            now.has_data
            and now.readiness_class == "red"
            and yesterday.has_data
            and yesterday.readiness_class == "red"
        )

    lower = _is_lower_body_day(conn, session.plan_day_id)
    acts = _endurance_rows(conn, day - timedelta(days=HARD_LOOKBACK_DAYS), day)
    flags = endurance.interference(day, lower, acts)

    out = []
    for r in plan_exercise_rows(conn, plan.id, session.plan_day_id):
        rx = prescription(r, r, plan.progression_model)
        hist = exposures(conn, r.exercise_id, day, plan, deloads)
        p = progression.next_prescription(rx, hist, plan.start_date)
        if deload_week:
            p = progression.deload(rx, p)
        else:
            p = progression.apply_modifiers(rx, p, readiness_class, bool(flags))
        out.append(_proposal_out(r, rx.model, p))
    return {
        "session": {
            "scheduled_session_id": session.id,
            "plan_id": plan.id,
            "label": session.label,
            "date": iso(day),
            "week_no": session.week_no,
            "status": session.status,
            "deload": deload_week,
            "readiness": readiness_out,
            "suggest_move": suggest_move,
            "interference_flags": [
                {
                    "activity_id": f.activity_id,
                    "date": iso(f.local_date),
                    "sport": f.sport,
                    "reasons": list(f.reasons),
                }
                for f in flags
            ],
        },
        "exercises": out,
        "note": "Nothing is saved. Weights in lb; dumbbells per hand.",
    }


def _top(e: Exposure) -> dict[str, Any]:
    weight, reps = progression.at_working_weight(e)
    return {"date": iso(e.local_date), "weight_lb": _num_lb(weight), "reps": list(reps)}


def evaluate(conn: Connection, plan_id: int | None, today: date) -> dict[str, Any]:
    if plan_id is None:
        plan = _active_plan(conn)
        if plan is None:
            raise ToolError("No active plan. Pass plan_id.")
    else:
        plan = conn.execute(select(plans).where(plans.c.id == plan_id)).first()
        if plan is None:
            raise ToolError(f"No plan with id {plan_id}.")
    if plan.start_date is None:
        raise ToolError(f"Plan {plan.id} is a draft with no start date; activate it first.")

    sessions = [
        SessionFact(s.scheduled_date, s.status)
        for s in conn.execute(
            select(scheduled_sessions.c.scheduled_date, scheduled_sessions.c.status)
            .join(plan_days, plan_days.c.id == scheduled_sessions.c.plan_day_id)
            .where(plan_days.c.plan_id == plan.id)
        )
    ]
    deloads = deload_weeks(conn, plan)
    end = min(today, plan.actual_end_date) if plan.actual_end_date else today
    cutoff = end + timedelta(days=1)  # include today's session

    lifts = []
    main_stalls = {}
    for exercise_id, (name, rx) in plan_prescriptions(conn, plan).items():
        hist = [
            e
            for e in exposures(conn, exercise_id, cutoff, plan, deloads)
            if e.local_date >= plan.start_date and not e.deload
        ]
        events = progression.stall_events(rx, hist)
        if rx.role == "main":
            main_stalls[exercise_id] = events
        lifts.append(
            {
                "exercise_id": exercise_id,
                "name": name,
                "role": rx.role,
                "exposures": len(hist),
                "first": _top(hist[0]) if hist else None,
                "last": _top(hist[-1]) if hist else None,
                "stall_streak": progression.stall_streak(rx, hist),
                "resets": sum(1 for e in events if e.flag == "stall"),
            }
        )
    criteria = plan_eval.end_criteria(plan.end_criteria)
    result = plan_eval.evaluate(
        start_date=plan.start_date,
        weeks=plan.weeks,
        planned_end_date=plan.planned_end_date,
        criteria=criteria,
        sessions=sessions,
        main_lift_stalls=main_stalls,
        today=today,
    )
    names = {lift["exercise_id"]: lift["name"] for lift in lifts}
    return {
        "plan_id": plan.id,
        "name": plan.name,
        "status": plan.status,
        "recommendation": result.recommendation,
        "reasons": list(result.reasons),
        "week": result.week,
        "weeks": result.weeks,
        "adherence": None if result.adherence is None else num(result.adherence, 2),
        "sessions_due": result.sessions_due,
        "sessions_completed": result.sessions_completed,
        "adherence_window_weeks": criteria.adherence_window_weeks,
        "reset_lifts": [{"exercise_id": i, "name": names[i]} for i in result.reset_lifts],
        "end_criteria": {
            "max_weeks": criteria.max_weeks,
            "stall_lifts": criteria.stall_lifts,
            "min_adherence": float(criteria.min_adherence),
            "adherence_window_weeks": criteria.adherence_window_weeks,
        },
        "lifts": lifts,
        "goal": _goal_progress(conn, plan.goal_id),
    }


def _goal_progress(conn: Connection, goal_id: int | None) -> dict[str, Any] | None:
    """Display only: A.10 does not read the goal."""
    if goal_id is None:
        return None
    g = conn.execute(select(goals).where(goals.c.id == goal_id)).one()
    out: dict[str, Any] = {
        "goal_id": g.id,
        "description": g.description,
        "status": g.status,
        "target_date": iso(g.target_date),
    }
    if g.target_unit == E1RM_UNIT and g.target_exercise_id is not None:
        latest = conn.execute(
            select(exercise_session_stats.c.best_e1rm_kg, exercise_session_stats.c.local_date)
            .where(
                exercise_session_stats.c.exercise_id == g.target_exercise_id,
                exercise_session_stats.c.best_e1rm_kg.is_not(None),
            )
            .order_by(exercise_session_stats.c.local_date.desc())
            .limit(1)
        ).first()
        out["target_e1rm_lb"] = lb(g.target_value)
        out["latest_e1rm_lb"] = None if latest is None else lb(latest.best_e1rm_kg)
        out["latest_date"] = None if latest is None else iso(latest.local_date)
    return out


def register(mcp: FastMCP, deps: Deps) -> None:
    @mcp.tool(annotations=READ_ONLY)
    def propose_next_session(scheduled_session_id: int) -> dict[str, Any]:
        """Propose sets, reps, load (lb), RIR and rest for each exercise of a planned session,
        from history and the progression rules, with rule_fired and reasons. Readiness
        modifiers apply only when the session is today; endurance interference and deload
        weeks apply on any date. needs_calibration means no usable history: ask the athlete
        for a recent working weight. Use this before suggesting any weight. Saves nothing."""
        with deps.engine.connect() as conn:
            return propose(conn, scheduled_session_id, deps.today(conn))

    @mcp.tool(annotations=READ_ONLY)
    def evaluate_plan(plan_id: int | None = None) -> dict[str, Any]:
        """Evaluate a plan (default: the active one): week, adherence over the recent window,
        per-lift progress and stalls, and a recommendation: continue, adjust (adherence low)
        or end (weeks done or too many main lifts reset). Use for weekly reviews and before
        proposing a new block."""
        with deps.engine.connect() as conn:
            return evaluate(conn, plan_id, deps.today(conn))
