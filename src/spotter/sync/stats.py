"""Derive `exercise_session_stats` from `performed_sets` via the engine.

Rebuilds upsert on (activity_id, exercise_id). When the activity is linked to a
scheduled session, each row gets that `scheduled_session_id`, and `met_prescription`
for exercises the session prescribed (null otherwise). Callers own the transaction.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Connection, and_, delete, select
from sqlalchemy.dialects.postgresql import insert

from spotter.db.schema import (
    activities,
    exercise_session_stats,
    exercises,
    performed_sets,
    prescribed_sets,
    scheduled_sessions,
)
from spotter.engine.sets import met_prescription, summarize
from spotter.engine.types import LoadedSet, SetPerformance
from spotter.units import kg_to_lb

STORAGE_KG = Decimal("0.001")  # NUMERIC(7,3)
VOLUME_KG = Decimal("0.01")  # NUMERIC(10,2)


def _q(value: Decimal | None, step: Decimal) -> Decimal | None:
    return None if value is None else value.quantize(step, ROUND_HALF_UP)


def _loaded(weight_kg: Decimal | None, reps: int | None) -> LoadedSet:
    return LoadedSet(None if not weight_kg else kg_to_lb(weight_kg), reps or 0)


def _prescription(
    conn: Connection, activity_id: int
) -> tuple[int | None, dict[int, list[LoadedSet]]]:
    """The linked session id and its prescribed sets per exercise, if any."""
    session_id = conn.execute(
        select(scheduled_sessions.c.id).where(scheduled_sessions.c.activity_id == activity_id)
    ).scalar()
    targets: dict[int, list[LoadedSet]] = defaultdict(list)
    if session_id is not None:
        for r in conn.execute(
            select(
                prescribed_sets.c.exercise_id,
                prescribed_sets.c.target_weight_kg,
                prescribed_sets.c.target_reps,
            )
            .where(prescribed_sets.c.scheduled_session_id == session_id)
            .order_by(prescribed_sets.c.set_no)
        ):
            targets[r.exercise_id].append(_loaded(r.target_weight_kg, r.target_reps))
    return session_id, targets


def rebuild_activity_stats(conn: Connection, activity_id: int) -> int:
    """Recompute stats for one activity. Returns the number of exercise rows kept."""
    rows = conn.execute(
        select(
            performed_sets.c.exercise_id,
            performed_sets.c.weight_kg,
            performed_sets.c.reps,
            exercises.c.e1rm_eligible,
            activities.c.local_date,
        )
        .join(exercises, exercises.c.id == performed_sets.c.exercise_id)
        .join(activities, activities.c.garmin_activity_id == performed_sets.c.activity_id)
        .where(performed_sets.c.activity_id == activity_id)
        .order_by(performed_sets.c.set_index)
    ).all()

    grouped: dict[int, list[SetPerformance]] = defaultdict(list)
    meta = {}
    for r in rows:
        grouped[r.exercise_id].append(SetPerformance(r.weight_kg, r.reps))
        meta[r.exercise_id] = (r.e1rm_eligible, r.local_date)

    session_id, targets = _prescription(conn, activity_id)
    kept: list[int] = []
    for exercise_id, sets in grouped.items():
        eligible, local_date = meta[exercise_id]
        summary = summarize(sets, eligible)
        if summary is None:
            continue
        values = {
            "local_date": local_date,
            "top_set_weight_kg": _q(summary.top_set_weight_kg, STORAGE_KG),
            "top_set_reps": summary.top_set_reps,
            "best_e1rm_kg": _q(summary.best_e1rm_kg, STORAGE_KG),
            "total_reps": summary.total_reps,
            "volume_kg": _q(summary.volume_kg, VOLUME_KG),
            "working_sets": summary.working_sets,
            "scheduled_session_id": session_id,
            "met_prescription": None
            if exercise_id not in targets
            else met_prescription(
                [_loaded(x.weight_kg, x.reps) for x in sets], targets[exercise_id]
            ),
        }
        stmt = insert(exercise_session_stats).values(
            activity_id=activity_id, exercise_id=exercise_id, **values
        )
        conn.execute(
            stmt.on_conflict_do_update(index_elements=["activity_id", "exercise_id"], set_=values)
        )
        kept.append(exercise_id)

    stale = exercise_session_stats.c.activity_id == activity_id
    if kept:
        stale = and_(stale, exercise_session_stats.c.exercise_id.not_in(kept))
    conn.execute(delete(exercise_session_stats).where(stale))
    return len(kept)


def rebuild_all(conn: Connection, exercise_id: int | None = None) -> int:
    """Rebuild every activity with sets, or only those touching one exercise.

    Use after curation changes `e1rm_eligible` or a set is remapped.
    Returns the number of activities rebuilt.
    """
    query = select(performed_sets.c.activity_id).distinct()
    if exercise_id is not None:
        in_stats = select(exercise_session_stats.c.activity_id).where(
            exercise_session_stats.c.exercise_id == exercise_id
        )
        query = query.where(
            (performed_sets.c.exercise_id == exercise_id)
            | performed_sets.c.activity_id.in_(in_stats)
        )
    ids = conn.execute(query).scalars().all()
    for activity_id in ids:
        rebuild_activity_stats(conn, activity_id)
    return len(ids)
