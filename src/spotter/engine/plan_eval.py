"""Plan evaluation (coaching-rules.md A.10): weeks, adherence, stalls, recommendation."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from spotter.engine.rules import (
    DEFAULT_ADHERENCE_WINDOW_WEEKS,
    DEFAULT_MAX_WEEKS,
    DEFAULT_MIN_ADHERENCE,
    DEFAULT_STALL_LIFTS,
)
from spotter.engine.types import (
    EndCriteria,
    PlanEvaluation,
    Recommendation,
    SessionFact,
    StallEvent,
)

DONE = "completed"
EXCLUDED = frozenset({"moved"})  # a moved session is not due; skipped counts as missed


def end_criteria(raw: Mapping[str, Any] | None) -> EndCriteria:
    """Stored `end_criteria` JSON over the A.10 defaults."""
    raw = raw or {}
    return EndCriteria(
        max_weeks=int(raw.get("max_weeks", DEFAULT_MAX_WEEKS)),
        stall_lifts=int(raw.get("stall_lifts", DEFAULT_STALL_LIFTS)),
        min_adherence=Decimal(str(raw.get("min_adherence", DEFAULT_MIN_ADHERENCE))),
        adherence_window_weeks=int(
            raw.get("adherence_window_weeks", DEFAULT_ADHERENCE_WINDOW_WEEKS)
        ),
    )


def plan_week(start: date | None, today: date) -> int | None:
    """1-based week of the block; week 1 is the 7 days starting at `start`."""
    if start is None or today < start:
        return None
    return (today - start).days // 7 + 1


def evaluate(
    *,
    start_date: date | None,
    weeks: int,
    planned_end_date: date | None,
    criteria: EndCriteria,
    sessions: Iterable[SessionFact],
    main_lift_stalls: Mapping[int, Sequence[StallEvent]],
    today: date,
) -> PlanEvaluation:
    """`end` when the weeks are done or `stall_lifts` main lifts reset; `adjust` when
    adherence over the window is below `min_adherence`; else `continue`.

    Adherence counts sessions dated in the `adherence_window_weeks` before today.
    """
    week = plan_week(start_date, today)
    limit = min(weeks, criteria.max_weeks)
    reasons: list[str] = []

    window_start = today - timedelta(weeks=criteria.adherence_window_weeks)
    due = [
        s for s in sessions if window_start <= s.scheduled_date < today and s.status not in EXCLUDED
    ]
    completed = sum(1 for s in due if s.status == DONE)
    adherence = Decimal(completed) / len(due) if due else None

    reset_lifts = tuple(
        sorted(
            ex for ex, events in main_lift_stalls.items() if any(e.flag == "stall" for e in events)
        )
    )

    recommendation: Recommendation = "continue"
    if week is not None and week > limit:
        reasons.append(f"weeks_done:{limit}")
    if planned_end_date is not None and today > planned_end_date:
        reasons.append(f"past_planned_end:{planned_end_date.isoformat()}")
    if len(reset_lifts) >= criteria.stall_lifts:
        reasons.append(f"stalled_main_lifts:{len(reset_lifts)}>={criteria.stall_lifts}")
    if reasons:
        recommendation = "end"
    elif adherence is not None and adherence < criteria.min_adherence:
        recommendation = "adjust"
        reasons.append(f"adherence:{completed}/{len(due)}<{criteria.min_adherence}")
    if adherence is None:
        reasons.append("adherence:no_sessions_due")
    return PlanEvaluation(
        recommendation=recommendation,
        week=week,
        weeks=weeks,
        adherence=adherence,
        sessions_due=len(due),
        sessions_completed=completed,
        reset_lifts=reset_lifts,
        reasons=tuple(reasons),
    )
