"""MCP tools, one module per group. Each module exposes `register(mcp, deps)`."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from spotter.db.schema import sync_state
from spotter.garmin.errors import GarminError
from spotter.mcp.deps import Deps
from spotter.mcp.serialize import iso
from spotter.sync.sync import MIN_INTERVAL, SOURCE, run_sync

READ_ONLY = {"readOnlyHint": True, "openWorldHint": False}


def last_synced_at(deps: Deps) -> Any:
    with deps.engine.connect() as conn:
        return conn.execute(
            select(sync_state.c.last_synced_at).where(sync_state.c.source == SOURCE)
        ).scalar()


def garmin_sync(
    deps: Deps, *, force: bool, full: bool = False, since: Any = None
) -> dict[str, Any]:
    """Run one budgeted sync. Raises GarminError; callers decide how to report it.

    Without `force`, a sync completed in the last 10 minutes is skipped before any
    Garmin session opens, so the snapshot does not touch Garmin on every call.
    """
    if not force:
        last = last_synced_at(deps)
        if last is not None and deps.now() - last < MIN_INTERVAL:
            return {"skipped": "recent", "last_synced_at": iso(last)}
    with deps.garmin(deps.engine) as session:
        result = run_sync(
            deps.engine,
            session.client,
            since=since,
            full=full,
            force=force,
            lock_engine=deps.lock_engine,
            now=deps.now,
        )
    out = result.as_dict()
    out["tokens_refreshed"] = session.refreshed
    out["last_synced_at"] = iso(last_synced_at(deps))
    return out


__all__ = ["READ_ONLY", "GarminError", "garmin_sync", "last_synced_at"]
