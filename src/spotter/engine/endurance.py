"""Running and cycling load, and interference with lower-body days (coaching-rules.md A.7)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal

from spotter.engine.rules import (
    INTERFERENCE_LOOKBACK_DAYS,
    LOWER_BODY_PATTERNS,
    RIDE_AEROBIC_TE_HARD,
    RIDE_LONG_S,
    RIDE_TYPES,
    RUN_AEROBIC_TE_HARD,
    RUN_ANAEROBIC_TE_HARD,
    RUN_LONG_S,
    RUN_TYPES,
)
from spotter.engine.types import (
    EnduranceActivity,
    InterferenceFlag,
    Sport,
    SportTotals,
    WeekTotals,
)


def sport(activity_type: str) -> Sport | None:
    if activity_type in RUN_TYPES:
        return "run"
    if activity_type in RIDE_TYPES:
        return "ride"
    return None


def hard_reasons(a: EnduranceActivity) -> list[str]:
    """Why a run or ride counts as hard or long. Empty for easy sessions and other sports."""
    kind = sport(a.activity_type)
    if kind is None:
        return []
    if kind == "run":
        long_s, aerobic = RUN_LONG_S, RUN_AEROBIC_TE_HARD
    else:
        long_s, aerobic = RIDE_LONG_S, RIDE_AEROBIC_TE_HARD
    reasons = []
    if a.duration_s is not None and a.duration_s >= long_s:
        reasons.append(f"{kind}_duration_s:{a.duration_s}>={long_s}")
    if a.aerobic_te is not None and a.aerobic_te >= aerobic:
        reasons.append(f"{kind}_aerobic_te:{a.aerobic_te}>={aerobic}")
    if kind == "run" and a.anaerobic_te is not None and a.anaerobic_te >= RUN_ANAEROBIC_TE_HARD:
        reasons.append(f"run_anaerobic_te:{a.anaerobic_te}>={RUN_ANAEROBIC_TE_HARD}")
    return reasons


def is_lower_body_day(main_patterns: Iterable[str | None]) -> bool:
    """True when any `main` exercise of the day is a squat, hinge or lunge."""
    return any(p in LOWER_BODY_PATTERNS for p in main_patterns)


def interference(
    planned_date: date, lower_body: bool, activities: Iterable[EnduranceActivity]
) -> list[InterferenceFlag]:
    """Hard runs and rides on the planned date or the 2 dates before it.

    Only lower-body days are flagged. Effect (Phase 4): no load increase on
    lower-body main lifts.
    """
    if not lower_body:
        return []
    earliest = planned_date - timedelta(days=INTERFERENCE_LOOKBACK_DAYS)
    flags = []
    for a in activities:
        kind = sport(a.activity_type)
        if kind is None or not earliest <= a.local_date <= planned_date:
            continue
        reasons = hard_reasons(a)
        if reasons:
            flags.append(InterferenceFlag(a.activity_id, a.local_date, kind, tuple(reasons)))
    return sorted(flags, key=lambda f: (f.local_date, f.activity_id))


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def totals(acts: list[EnduranceActivity]) -> SportTotals:
    """Sum one sport's sessions. Missing values count as 0."""
    return SportTotals(
        sessions=len(acts),
        duration_s=sum(a.duration_s or 0 for a in acts),
        distance_m=sum((a.distance_m or Decimal(0) for a in acts), Decimal(0)),
        training_load=sum((a.training_load or Decimal(0) for a in acts), Decimal(0)),
    )


def weekly_totals(activities: Iterable[EnduranceActivity]) -> list[WeekTotals]:
    """Run and ride totals per Monday-start week, oldest first. Weeks with neither are left out."""
    weeks: dict[date, dict[Sport, list[EnduranceActivity]]] = {}
    for a in activities:
        kind = sport(a.activity_type)
        if kind is None:
            continue
        weeks.setdefault(week_start(a.local_date), {"run": [], "ride": []})[kind].append(a)
    return [
        WeekTotals(start, totals(by_sport["run"]), totals(by_sport["ride"]))
        for start, by_sport in sorted(weeks.items())
    ]
