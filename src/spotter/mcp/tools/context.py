"""Context tools (SPEC 11.1). Read-only, compact JSON, weights in lb, distances in km.

Query functions take a Connection and the athlete's local `today`, so they test
without MCP. `register` wires them as tools.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field
from sqlalchemy import Connection, Row, and_, func, or_, select

from spotter.db.schema import (
    activities,
    adjustments,
    athlete,
    daily_metrics,
    exercise_session_stats,
    exercises,
    performed_sets,
    plan_days,
    plan_exercises,
    plans,
    scheduled_sessions,
)
from spotter.engine import endurance, readiness, trend
from spotter.engine.types import DailyReadinessInput, EnduranceActivity, ReadinessDay, TrendPoint
from spotter.garmin.mappers import STRENGTH_TYPE
from spotter.mcp.deps import Deps
from spotter.mcp.serialize import iso, km, lb, minutes, num
from spotter.mcp.tools import READ_ONLY, GarminError, garmin_sync

RECENT_SESSIONS = 3
STRENGTH_WINDOW_DAYS = 28
READINESS_WINDOWS = (7, 28)
ENDURANCE_SNAPSHOT_DAYS = 7
HARD_LOOKBACK_DAYS = 2  # "last 48 h": today and the 2 dates before
OPEN_SESSION = ("planned", "pushed")


# Shared pieces --------------------------------------------------------------


def _window(today: date, days: int) -> date:
    """First date of a `days`-long window ending today."""
    return today - timedelta(days=days - 1)


def _endurance_rows(conn: Connection, start: date, end: date) -> list[EnduranceActivity]:
    rows = conn.execute(
        select(activities)
        .where(activities.c.local_date.between(start, end), activities.c.type != STRENGTH_TYPE)
        .order_by(activities.c.start_time)
    )
    return [
        EnduranceActivity(
            activity_id=r.garmin_activity_id,
            local_date=r.local_date,
            activity_type=r.type,
            duration_s=r.duration_s,
            distance_m=r.distance_m,
            training_load=r.training_load,
            aerobic_te=r.aerobic_te,
            anaerobic_te=r.anaerobic_te,
        )
        for r in rows
    ]


def _sport_totals(t: Any) -> dict[str, Any]:
    return {
        "sessions": t.sessions,
        "duration_min": minutes(t.duration_s),
        "distance_km": km(t.distance_m),
        "training_load": num(t.training_load),
    }


def _readiness_days(
    conn: Connection, start: date, end: date
) -> list[tuple[Row[Any] | None, ReadinessDay]]:
    """One classified day per date, oldest first. Dates with no row classify as no data."""
    rows = {
        r.local_date: r
        for r in conn.execute(
            select(daily_metrics).where(daily_metrics.c.local_date.between(start, end))
        )
    }
    out = []
    day = start
    while day <= end:
        r = rows.get(day)
        inp = DailyReadinessInput(local_date=day)
        if r is not None:
            inp = DailyReadinessInput(
                local_date=day,
                hrv_status=r.hrv_status,
                hrv_overnight_ms=r.hrv_overnight_ms,
                hrv_baseline_low=r.hrv_baseline_low,
                training_readiness=r.training_readiness,
                sleep_score=r.sleep_score,
                body_battery_max=r.body_battery_max,
            )
        out.append((r, readiness.classify(inp)))
        day += timedelta(days=1)
    return out


def _class(day: ReadinessDay) -> str:
    return day.readiness_class if day.has_data else "no_data"


def _window_counts(days: list[ReadinessDay]) -> dict[str, int]:
    w = readiness.summarize_window(days)
    return {"days": w.days, "green": w.green, "amber": w.amber, "red": w.red, "no_data": w.no_data}


def _top_set(weight_kg: Any, reps: int | None) -> dict[str, Any]:
    return {"weight_lb": lb(weight_kg), "reps": reps}


def _trend_out(points: list[TrendPoint]) -> dict[str, Any]:
    t = trend.trend(points)
    in_lb = t.metric in ("e1rm", "top_set_weight")
    conv = lb if in_lb else num
    return {
        "metric": t.metric,
        "direction": t.direction,
        "first": conv(t.first),
        "last": conv(t.last),
        "change": conv(t.change) if in_lb else num(t.change),
        "change_pct": None
        if t.change_fraction is None
        else round(float(t.change_fraction) * 100, 1),
        "points": t.points,
        "unit": "lb" if in_lb else ("reps" if t.metric else None),
    }


# Plans ----------------------------------------------------------------------


def _plan_header(conn: Connection, plan: Row[Any], today: date) -> dict[str, Any]:
    week = None
    if plan.start_date is not None and today >= plan.start_date:
        week = min((today - plan.start_date).days // 7 + 1, plan.weeks)
    nxt = conn.execute(
        select(scheduled_sessions, plan_days.c.label)
        .join(plan_days, plan_days.c.id == scheduled_sessions.c.plan_day_id)
        .where(
            plan_days.c.plan_id == plan.id,
            scheduled_sessions.c.scheduled_date >= today,
            scheduled_sessions.c.status.in_(OPEN_SESSION),
        )
        .order_by(scheduled_sessions.c.scheduled_date)
        .limit(1)
    ).first()
    return {
        "id": plan.id,
        "name": plan.name,
        "status": plan.status,
        "goal_id": plan.goal_id,
        "week": week,
        "weeks": plan.weeks,
        "sessions_per_week": plan.sessions_per_week,
        "start_date": iso(plan.start_date),
        "planned_end_date": iso(plan.planned_end_date),
        "progression_model": plan.progression_model,
        "next_session": None
        if nxt is None
        else {
            "scheduled_session_id": nxt.id,
            "label": nxt.label,
            "date": iso(nxt.scheduled_date),
            "week_no": nxt.week_no,
            "status": nxt.status,
            "plan_day_id": nxt.plan_day_id,
        },
    }


def _active_plan(conn: Connection) -> Row[Any] | None:
    return conn.execute(select(plans).where(plans.c.status == "active")).first()


def _is_lower_body_day(conn: Connection, plan_day_id: int) -> bool:
    patterns = conn.execute(
        select(exercises.c.movement_pattern)
        .join(plan_exercises, plan_exercises.c.exercise_id == exercises.c.id)
        .where(plan_exercises.c.plan_day_id == plan_day_id, plan_exercises.c.role == "main")
    ).scalars()
    return endurance.is_lower_body_day(patterns)


def plan_detail(conn: Connection, today: date, plan_id: int | None) -> dict[str, Any]:
    if plan_id is None:
        plan = _active_plan(conn)
        if plan is None:
            return {"plan": None, "note": "No active plan."}
    else:
        plan = conn.execute(select(plans).where(plans.c.id == plan_id)).first()
        if plan is None:
            raise ToolError(f"No plan with id {plan_id}.")
    days = conn.execute(
        select(plan_days).where(plan_days.c.plan_id == plan.id).order_by(plan_days.c.day_order)
    ).all()
    day_ids = [d.id for d in days]
    by_day: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for e in conn.execute(
        select(plan_exercises, exercises.c.display_name)
        .join(exercises, exercises.c.id == plan_exercises.c.exercise_id)
        .where(plan_exercises.c.plan_day_id.in_(day_ids))
        .order_by(plan_exercises.c.plan_day_id, plan_exercises.c.position)
    ):
        by_day[e.plan_day_id].append(
            {
                "plan_exercise_id": e.id,
                "exercise_id": e.exercise_id,
                "name": e.display_name,
                "position": e.position,
                "superset_group": e.superset_group,
                "role": e.role,
                "sets": e.sets,
                "rep_min": e.rep_min,
                "rep_max": e.rep_max,
                "target_rir": e.target_rir,
                "rest_s": e.rest_s,
            }
        )
    schedule = conn.execute(
        select(scheduled_sessions, plan_days.c.label)
        .join(plan_days, plan_days.c.id == scheduled_sessions.c.plan_day_id)
        .where(plan_days.c.plan_id == plan.id)
        .order_by(scheduled_sessions.c.scheduled_date)
    )
    log = conn.execute(
        select(adjustments)
        .where(adjustments.c.plan_id == plan.id)
        .order_by(adjustments.c.created_at)
    )
    return {
        "plan": {
            **_plan_header(conn, plan, today),
            "deload_week": plan.deload_week,
            "actual_end_date": iso(plan.actual_end_date),
            "end_criteria": plan.end_criteria,
            "rationale": plan.rationale,
            "outcome_summary": plan.outcome_summary,
        },
        "days": [
            {
                "plan_day_id": d.id,
                "label": d.label,
                "day_order": d.day_order,
                "preferred_weekday": d.preferred_weekday,
                "exercises": by_day[d.id],
            }
            for d in days
        ],
        "schedule": [
            {
                "scheduled_session_id": s.id,
                "plan_day_id": s.plan_day_id,
                "label": s.label,
                "week_no": s.week_no,
                "date": iso(s.scheduled_date),
                "status": s.status,
                "activity_id": s.activity_id,
            }
            for s in schedule
        ],
        "adjustments": [
            {
                "id": a.id,
                "created_at": iso(a.created_at),
                "kind": a.kind,
                "reason": a.reason,
                "payload": a.payload,
            }
            for a in log
        ],
    }


# Strength -------------------------------------------------------------------


def _session_exercises(
    conn: Connection, activity_ids: list[int]
) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in conn.execute(
        select(exercise_session_stats, exercises.c.display_name)
        .join(exercises, exercises.c.id == exercise_session_stats.c.exercise_id)
        .where(exercise_session_stats.c.activity_id.in_(activity_ids))
        .order_by(exercises.c.display_name)
    ):
        out[r.activity_id].append(
            {
                "exercise_id": r.exercise_id,
                "name": r.display_name,
                "top_set": _top_set(r.top_set_weight_kg, r.top_set_reps),
                "working_sets": r.working_sets,
                "total_reps": r.total_reps,
                "volume_lb": lb(r.volume_kg),
                "e1rm_lb": lb(r.best_e1rm_kg),
            }
        )
    return out


def recent_strength(conn: Connection, limit: int = RECENT_SESSIONS) -> list[dict[str, Any]]:
    sessions = conn.execute(
        select(activities.c.garmin_activity_id, activities.c.local_date, activities.c.duration_s)
        .where(activities.c.type == STRENGTH_TYPE)
        .order_by(activities.c.start_time.desc())
        .limit(limit)
    ).all()
    ids = [s.garmin_activity_id for s in sessions]
    per = _session_exercises(conn, ids)
    unmapped: dict[int, int] = dict(
        conn.execute(
            select(performed_sets.c.activity_id, func.count())
            .where(performed_sets.c.activity_id.in_(ids), performed_sets.c.exercise_id.is_(None))
            .group_by(performed_sets.c.activity_id)
        ).all()
    )
    return [
        {
            "activity_id": s.garmin_activity_id,
            "date": iso(s.local_date),
            "duration_min": minutes(s.duration_s),
            "exercises": per[s.garmin_activity_id],
            "unmapped_sets": unmapped.get(s.garmin_activity_id, 0),
            "planned": None,  # linked to scheduled sessions in Phase 5
        }
        for s in sessions
    ]


def strength_window(
    conn: Connection, today: date, days: int = STRENGTH_WINDOW_DAYS
) -> dict[str, Any]:
    """Per-exercise rollup over the window (decisions.md, "Snapshot 28-day strength rollup")."""
    start = _window(today, days)
    session_dates = (
        conn.execute(
            select(activities.c.local_date)
            .where(
                activities.c.type == STRENGTH_TYPE, activities.c.local_date.between(start, today)
            )
            .order_by(activities.c.local_date)
        )
        .scalars()
        .all()
    )
    rows = conn.execute(
        select(exercise_session_stats, exercises.c.display_name)
        .join(exercises, exercises.c.id == exercise_session_stats.c.exercise_id)
        .where(exercise_session_stats.c.local_date.between(start, today))
        .order_by(exercise_session_stats.c.local_date)
    ).all()
    grouped: dict[int, list[Row[Any]]] = defaultdict(list)
    for r in rows:
        grouped[r.exercise_id].append(r)
    per_exercise = []
    for exercise_id, rs in grouped.items():
        last = rs[-1]
        per_exercise.append(
            {
                "exercise_id": exercise_id,
                "name": last.display_name,
                "exposures": len(rs),
                "first_date": iso(rs[0].local_date),
                "last_date": iso(last.local_date),
                "last_top_set": _top_set(last.top_set_weight_kg, last.top_set_reps),
                "working_sets": sum(r.working_sets or 0 for r in rs),
                "volume_lb": lb(sum((r.volume_kg or Decimal(0) for r in rs), Decimal(0))),
                "trend": _trend_out(
                    [
                        TrendPoint(
                            r.local_date, r.best_e1rm_kg, r.top_set_weight_kg, r.top_set_reps
                        )
                        for r in rs
                    ]
                ),
            }
        )
    per_exercise.sort(key=lambda e: (-e["exposures"], e["name"]))
    return {
        "days": days,
        "start": iso(start),
        "end": iso(today),
        "sessions": len(session_dates),
        "session_dates": [iso(d) for d in session_dates],
        "exercises": per_exercise,
    }


def exercise_history(
    conn: Connection, exercise_id: int, since: date | None, limit: int
) -> dict[str, Any]:
    ex = conn.execute(select(exercises).where(exercises.c.id == exercise_id)).first()
    if ex is None:
        raise ToolError(f"No exercise with id {exercise_id}. Use search_exercises to find one.")
    query = select(exercise_session_stats).where(
        exercise_session_stats.c.exercise_id == exercise_id
    )
    if since is not None:
        query = query.where(exercise_session_stats.c.local_date >= since)
    rows = conn.execute(
        query.order_by(exercise_session_stats.c.local_date.desc()).limit(limit)
    ).all()
    return {
        "exercise": {"id": ex.id, "name": ex.display_name, "e1rm_eligible": ex.e1rm_eligible},
        "sessions": [
            {
                "date": iso(r.local_date),
                "activity_id": r.activity_id,
                "top_set": _top_set(r.top_set_weight_kg, r.top_set_reps),
                "e1rm_lb": lb(r.best_e1rm_kg),
                "volume_lb": lb(r.volume_kg),
                "working_sets": r.working_sets,
                "total_reps": r.total_reps,
                "met_prescription": r.met_prescription,
            }
            for r in rows
        ],
        "trend": _trend_out(
            [
                TrendPoint(r.local_date, r.best_e1rm_kg, r.top_set_weight_kg, r.top_set_reps)
                for r in rows
            ]
        ),
    }


def open_flags(conn: Connection, today: date) -> dict[str, Any]:
    unmapped = conn.execute(
        select(
            performed_sets.c.garmin_category,
            performed_sets.c.garmin_name,
            func.count().label("sets"),
            func.max(activities.c.local_date).label("last_date"),
        )
        .join(activities, activities.c.garmin_activity_id == performed_sets.c.activity_id)
        .where(performed_sets.c.exercise_id.is_(None))
        .group_by(performed_sets.c.garmin_category, performed_sets.c.garmin_name)
        .order_by(func.count().desc())
    )
    missed = conn.execute(
        select(scheduled_sessions, plan_days.c.label)
        .join(plan_days, plan_days.c.id == scheduled_sessions.c.plan_day_id)
        .where(
            scheduled_sessions.c.scheduled_date < today,
            scheduled_sessions.c.status.in_(OPEN_SESSION),
        )
        .order_by(scheduled_sessions.c.scheduled_date)
    )
    return {
        "unmapped_exercises": [
            {
                "garmin_category": u.garmin_category,
                "garmin_name": u.garmin_name,
                "sets": u.sets,
                "last_date": iso(u.last_date),
            }
            for u in unmapped
        ],
        "missed_sessions": [
            {
                "scheduled_session_id": m.id,
                "label": m.label,
                "date": iso(m.scheduled_date),
                "status": m.status,
            }
            for m in missed
        ],
        # Null, not []: stall detection arrives in Phase 4 (engine.progression), and an
        # empty list read as "no stalls" in the Phase 3 exit check.
        "stalls": None,
        "stalls_note": "Stall detection is not available yet. Do not report stalls as absent.",
    }


# Readiness and endurance ----------------------------------------------------


def _metric_row(r: Row[Any] | None, day: ReadinessDay) -> dict[str, Any]:
    out: dict[str, Any] = {
        "date": iso(day.local_date),
        "class": _class(day),
        "reasons": list(day.reasons),
    }
    if r is not None:
        out.update(
            {
                "resting_hr": r.resting_hr,
                "hrv_overnight_ms": r.hrv_overnight_ms,
                "hrv_status": r.hrv_status,
                "sleep_score": r.sleep_score,
                "sleep_h": None if r.sleep_s is None else round(r.sleep_s / 3600, 1),
                "body_battery_max": r.body_battery_max,
                "body_battery_min": r.body_battery_min,
                "training_readiness": r.training_readiness,
                "acute_load": num(r.acute_load),
                "stress_avg": r.stress_avg,
            }
        )
    return out


def _avg(values: list[int | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(sum(present) / len(present), 1) if present else None


def readiness_detail(conn: Connection, today: date, days: int) -> dict[str, Any]:
    pairs = _readiness_days(conn, _window(today, days), today)
    rows = [r for r, _ in pairs if r is not None]
    latest_baseline = next(
        (
            r
            for r in reversed(rows)
            if r.hrv_baseline_low is not None or r.hrv_baseline_high is not None
        ),
        None,
    )

    def latest(attr: str) -> Any:
        return next((v for r in reversed(rows) if (v := getattr(r, attr)) is not None), None)

    return {
        "days": [_metric_row(r, d) for r, d in reversed(pairs)],  # newest first
        "summary": _window_counts([d for _, d in pairs]),
        "baselines": {
            "hrv_baseline_low_ms": None
            if latest_baseline is None
            else latest_baseline.hrv_baseline_low,
            "hrv_baseline_high_ms": None
            if latest_baseline is None
            else latest_baseline.hrv_baseline_high,
            "resting_hr_avg": _avg([r.resting_hr for r in rows]),
            "hrv_overnight_avg_ms": _avg([r.hrv_overnight_ms for r in rows]),
            "sleep_score_avg": _avg([r.sleep_score for r in rows]),
            "vo2max_run": num(latest("vo2max_run")),
            "vo2max_bike": num(latest("vo2max_bike")),
            "bodyweight_lb": lb(latest("bodyweight_kg")),
        },
    }


def _activity_out(a: EnduranceActivity) -> dict[str, Any]:
    return {
        "activity_id": a.activity_id,
        "date": iso(a.local_date),
        "type": a.activity_type,
        "duration_min": minutes(a.duration_s),
        "distance_km": km(a.distance_m),
        "training_load": num(a.training_load),
        "aerobic_te": num(a.aerobic_te),
        "anaerobic_te": num(a.anaerobic_te),
        "hard_reasons": endurance.hard_reasons(a),
    }


def endurance_detail(conn: Connection, today: date, days: int) -> dict[str, Any]:
    acts = _endurance_rows(conn, _window(today, days), today)
    return {
        "days": days,
        "activities": [_activity_out(a) for a in reversed(acts)],  # newest first
        "weekly_totals": [
            {
                "week_start": iso(w.week_start),
                "run": _sport_totals(w.run),
                "ride": _sport_totals(w.ride),
            }
            for w in endurance.weekly_totals(acts)
        ],
    }


def _endurance_snapshot(
    conn: Connection, today: date, next_session: dict[str, Any] | None
) -> dict[str, Any]:
    start = _window(today, ENDURANCE_SNAPSHOT_DAYS)
    acts = _endurance_rows(conn, start - timedelta(days=HARD_LOOKBACK_DAYS), today)
    recent = [a for a in acts if a.local_date >= start]
    hard_from = today - timedelta(days=HARD_LOOKBACK_DAYS)
    flags: list[dict[str, Any]] = []
    if next_session is not None:
        planned = date.fromisoformat(next_session["date"])
        lower = _is_lower_body_day(conn, next_session["plan_day_id"])
        flags = [
            {
                "activity_id": f.activity_id,
                "date": iso(f.local_date),
                "sport": f.sport,
                "reasons": list(f.reasons),
            }
            for f in endurance.interference(planned, lower, acts)
        ]
    return {
        "last_7_days": {
            "run": _sport_totals(
                endurance.totals([a for a in recent if endurance.sport(a.activity_type) == "run"])
            ),
            "ride": _sport_totals(
                endurance.totals([a for a in recent if endurance.sport(a.activity_type) == "ride"])
            ),
            "other_sessions": sum(1 for a in recent if endurance.sport(a.activity_type) is None),
        },
        "hard_last_48h": [
            _activity_out(a)
            for a in acts
            if a.local_date >= hard_from and endurance.hard_reasons(a)
        ],
        "interference_flags": flags,
    }


def _athlete(conn: Connection) -> dict[str, Any] | None:
    a = conn.execute(select(athlete)).first()
    if a is None:
        return None
    return {
        "time_zone": a.time_zone,
        "bodyweight_lb": lb(a.bodyweight_kg),
        "training_age_years": num(a.training_age_years),
        "available_days": a.available_days,
        "session_minutes": a.session_minutes,
        "equipment": a.equipment,
        "limitations": a.limitations,
    }


def snapshot(conn: Connection, today: date) -> dict[str, Any]:
    """Everything in the snapshot except the sync block."""
    plan_row = _active_plan(conn)
    plan = None if plan_row is None else _plan_header(conn, plan_row, today)
    pairs = _readiness_days(conn, _window(today, max(READINESS_WINDOWS)), today)
    days = [d for _, d in pairs]
    today_day = days[-1]
    return {
        "today": iso(today),
        "athlete": _athlete(conn),
        "plan": plan,
        "readiness": {
            "today": {"class": _class(today_day), "reasons": list(today_day.reasons)},
            **{f"last_{n}_days": _window_counts(days[-n:]) for n in READINESS_WINDOWS},
        },
        "endurance": _endurance_snapshot(
            conn, today, None if plan is None else plan["next_session"]
        ),
        "recent_strength": recent_strength(conn),
        "strength_28d": strength_window(conn, today),
        "flags": open_flags(conn, today),
    }


def search(
    conn: Connection, query: str, pattern: str | None, equipment: str | None, limit: int
) -> list[dict[str, Any]]:
    stmt = select(exercises)
    for token in query.split():
        like = "%" + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        # Garmin keys use underscores ("BENCH_PRESS"); match them with spaces too.
        key_like = like.replace(" ", "_")
        stmt = stmt.where(
            or_(
                exercises.c.display_name.ilike(like, escape="\\"),
                exercises.c.garmin_category.ilike(key_like, escape="\\"),
                exercises.c.garmin_name.ilike(key_like, escape="\\"),
            )
        )
    if pattern:
        stmt = stmt.where(exercises.c.movement_pattern == pattern)
    if equipment:
        eq = "%" + equipment.replace("%", "").replace("_", "\\_") + "%"
        stmt = stmt.where(
            or_(
                exercises.c.load_type.ilike(eq, escape="\\"),
                and_(
                    exercises.c.load_type.is_(None), exercises.c.display_name.ilike(eq, escape="\\")
                ),
            )
        )
    rows = conn.execute(
        stmt.order_by(
            exercises.c.curated.desc(),
            func.length(exercises.c.display_name),
            exercises.c.display_name,
        ).limit(limit)
    )
    return [
        {
            "id": r.id,
            "name": r.display_name,
            "garmin_category": r.garmin_category,
            "garmin_name": r.garmin_name,
            "curated": r.curated,
            "movement_pattern": r.movement_pattern,
            "primary_muscles": r.primary_muscles,
            "secondary_muscles": r.secondary_muscles,
            "load_type": r.load_type,
            "increment_lb": num(r.increment_lb, 2),
            "e1rm_eligible": r.e1rm_eligible,
        }
        for r in rows
    ]


# Registration ---------------------------------------------------------------


def register(mcp: FastMCP, deps: Deps) -> None:
    @mcp.tool(annotations={**READ_ONLY, "openWorldHint": True})
    def get_training_snapshot(force_sync: bool = False) -> dict[str, Any]:
        """Start here, once per conversation. Syncs Garmin (skipped if synced in the last
        10 minutes, unless force_sync) and returns: sync status, athlete profile, active plan,
        readiness today plus 7 and 28 day counts, endurance load and flags, the last 3 strength
        sessions, a 28-day per-exercise strength rollup, and open flags (unmapped exercises,
        missed sessions). If sync fails, `sync.error` says why and the rest uses stored data.
        Endurance here covers 7 days; use get_endurance_load for longer history.
        Loads in lb, distances in km."""
        try:
            sync = garmin_sync(deps, force=force_sync)
        except GarminError as exc:
            sync = {"error": str(exc)}
        with deps.engine.connect() as conn:
            return {"sync": sync, **snapshot(conn, deps.today(conn))}

    @mcp.tool(annotations=READ_ONLY)
    def get_exercise_history(
        exercise_id: int,
        since: date | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        """Per-session history of one exercise, newest first: top set, e1RM, volume, working
        sets, met_prescription, plus the trend across the returned sessions. e1RM is null until
        the exercise is curated as e1RM-eligible. Get ids from the snapshot or search_exercises."""
        with deps.engine.connect() as conn:
            return exercise_history(conn, exercise_id, since, limit)

    @mcp.tool(annotations=READ_ONLY)
    def get_readiness(days: Annotated[int, Field(ge=1, le=90)] = 14) -> dict[str, Any]:
        """Daily readiness rows (newest first) with green/amber/red class and reasons, class
        counts, and baselines (Garmin HRV baseline, window averages). Reads stored data only;
        call get_training_snapshot first to sync."""
        with deps.engine.connect() as conn:
            return readiness_detail(conn, deps.today(conn), days)

    @mcp.tool(annotations=READ_ONLY)
    def get_endurance_load(days: Annotated[int, Field(ge=1, le=180)] = 28) -> dict[str, Any]:
        """Non-strength activities in the last `days` days (newest first) with hard-session
        reasons, plus run and ride totals per Monday-start week. Distances in km."""
        with deps.engine.connect() as conn:
            return endurance_detail(conn, deps.today(conn), days)

    @mcp.tool(annotations=READ_ONLY)
    def get_plan(plan_id: int | None = None) -> dict[str, Any]:
        """A plan with its days, exercises, schedule and adjustments log. Defaults to the
        active plan; returns plan null when there is none."""
        with deps.engine.connect() as conn:
            return plan_detail(conn, deps.today(conn), plan_id)

    @mcp.tool(annotations=READ_ONLY)
    def search_exercises(
        query: Annotated[str, Field(min_length=1, max_length=100)],
        pattern: str | None = None,
        equipment: str | None = None,
        limit: Annotated[int, Field(ge=1, le=50)] = 20,
    ) -> dict[str, Any]:
        """Find exercises in the catalog. Every word of `query` must match the name or Garmin
        key. `pattern` filters curated movement pattern (squat, hinge, push_h, push_v, pull_h,
        pull_v, lunge, carry, core, isolation). `equipment` (e.g. barbell, dumbbell, cable)
        matches the curated load type, or the name when uncurated."""
        with deps.engine.connect() as conn:
            return {"exercises": search(conn, query, pattern, equipment, limit)}
