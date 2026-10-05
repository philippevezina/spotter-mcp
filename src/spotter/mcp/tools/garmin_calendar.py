"""Removing pushed sessions from Garmin. Shared by unschedule_session, close_plan and
activate_plan(replace=true).

Garmin first, database second: a removal that fails leaves the ids in place, and a
retry is safe because removing a missing item is not an error (decisions.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from fastmcp.exceptions import ToolError
from sqlalchemy import Connection, Row, delete, select, update

from spotter.db.schema import plan_days, prescribed_sets, scheduled_sessions
from spotter.garmin.errors import GarminError
from spotter.mcp.deps import Deps
from spotter.mcp.tools.context import OPEN_SESSION


def pushed_sessions(conn: Connection, plan_id: int, from_date: date) -> list[Row[Any]]:
    """Open sessions of a plan dated `from_date` or later that have a Garmin workout."""
    return list(
        conn.execute(
            select(
                scheduled_sessions.c.id,
                scheduled_sessions.c.garmin_workout_id,
                scheduled_sessions.c.garmin_schedule_id,
            )
            .join(plan_days, plan_days.c.id == scheduled_sessions.c.plan_day_id)
            .where(
                plan_days.c.plan_id == plan_id,
                scheduled_sessions.c.scheduled_date >= from_date,
                scheduled_sessions.c.status.in_(OPEN_SESSION),
                scheduled_sessions.c.garmin_workout_id.is_not(None),
            )
        )
    )


def remove_pushed(deps: Deps, rows: Sequence[Row[Any]]) -> None:
    """Take each session off the Garmin calendar, then delete its workout. Opens no
    Garmin session when there is nothing to remove. Garmin errors become tool errors."""
    if not rows:
        return
    try:
        with deps.garmin(deps.engine) as session:
            for r in rows:
                if r.garmin_schedule_id is not None:
                    session.client.unschedule_workout(r.garmin_schedule_id)
                if r.garmin_workout_id is not None:
                    session.client.delete_workout(r.garmin_workout_id)
    except GarminError as exc:
        raise ToolError(f"{exc} No session or plan was changed; it is safe to retry.") from exc


def clear_push(conn: Connection, session_ids: Sequence[int]) -> None:
    """Forget the Garmin ids and the prescription of sessions removed from Garmin."""
    if not session_ids:
        return
    conn.execute(
        update(scheduled_sessions)
        .where(scheduled_sessions.c.id.in_(session_ids))
        .values(garmin_workout_id=None, garmin_schedule_id=None)
    )
    conn.execute(
        delete(prescribed_sets).where(prescribed_sets.c.scheduled_session_id.in_(session_ids))
    )
