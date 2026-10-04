"""Admin tools (SPEC 11.4): manual sync and exercise mapping."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from sqlalchemy import Connection, func, select, update
from sqlalchemy.dialects.postgresql import insert

from spotter.db.schema import exercise_aliases, exercises, performed_sets
from spotter.mcp.deps import Deps
from spotter.mcp.tools import GarminError, garmin_sync
from spotter.sync.stats import rebuild_activity_stats

# Older history goes through `spotter backfill` locally: it has no time budget.
MAX_SINCE_DAYS = 60


def map_pair(
    conn: Connection, garmin_category: str, garmin_name: str, exercise_id: int
) -> dict[str, Any]:
    """Alias a Garmin pair to an exercise, remap stored sets, rebuild affected stats.

    Callers own the transaction.
    """
    in_catalog = conn.execute(
        select(exercises.c.id).where(
            exercises.c.garmin_category == garmin_category, exercises.c.garmin_name == garmin_name
        )
    ).scalar()
    if in_catalog is not None:
        raise ToolError(
            f"{garmin_category}/{garmin_name or '-'} is already in the catalog as exercise "
            f"{in_catalog}. Sync maps it automatically."
        )
    target = conn.execute(
        select(exercises.c.id, exercises.c.display_name).where(exercises.c.id == exercise_id)
    ).first()
    if target is None:
        raise ToolError(f"No exercise with id {exercise_id}. Use search_exercises to find one.")

    stmt = insert(exercise_aliases).values(
        garmin_category=garmin_category, garmin_name=garmin_name, exercise_id=exercise_id
    )
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=["garmin_category", "garmin_name"],
            set_={"exercise_id": stmt.excluded.exercise_id, "created_at": func.now()},
        )
    )
    remapped = (
        conn.execute(
            update(performed_sets)
            .where(
                performed_sets.c.garmin_category == garmin_category,
                performed_sets.c.garmin_name == garmin_name,
                performed_sets.c.exercise_id.is_distinct_from(exercise_id),
            )
            .values(exercise_id=exercise_id)
            .returning(performed_sets.c.activity_id)
        )
        .scalars()
        .all()
    )
    activity_ids = sorted(set(remapped))
    for activity_id in activity_ids:
        rebuild_activity_stats(conn, activity_id)
    return {
        "garmin_category": garmin_category,
        "garmin_name": garmin_name,
        "exercise_id": target.id,
        "exercise_name": target.display_name,
        "sets_remapped": len(remapped),
        "activities_rebuilt": len(activity_ids),
    }


def register(mcp: FastMCP, deps: Deps) -> None:
    @mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": True, "openWorldHint": True})
    def sync_garmin(full: bool = False, since: date | None = None) -> dict[str, Any]:
        """Sync Garmin now, ignoring the 10-minute minimum, within a 60 s budget. `full`
        re-reads sets of every strength session in the window. `since` (at most 60 days back)
        widens the window; older history needs the local backfill command. If the result says
        partial, call again to continue. get_training_snapshot already syncs, so use this only
        when the athlete asks or data looks stale."""
        if since is not None:
            with deps.engine.connect() as conn:
                earliest = deps.today(conn) - timedelta(days=MAX_SINCE_DAYS)
            if since < earliest:
                raise ToolError(
                    f"since must be on or after {earliest.isoformat()}. "
                    "Run `spotter backfill --since` locally for older history."
                )
        try:
            return garmin_sync(deps, force=True, full=full, since=since)
        except GarminError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool(annotations={"readOnlyHint": False, "idempotentHint": True, "openWorldHint": False})
    def map_exercise(
        garmin_category: str, exercise_id: int, garmin_name: str = ""
    ) -> dict[str, Any]:
        """Map an unmapped Garmin exercise (category and name, from the snapshot's
        flags.unmapped_exercises) to a catalog exercise. Future syncs use the mapping, stored
        sets are remapped and their stats rebuilt. Call only after the athlete confirms which
        exercise it is."""
        with deps.engine.begin() as conn:
            return map_pair(conn, garmin_category, garmin_name, exercise_id)
