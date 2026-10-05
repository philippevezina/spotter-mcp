"""Volume guardrails for create_plan (coaching-rules.md A.8). Warnings, never errors."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal

from spotter.engine.rules import (
    NEW_PLAN_SETS_HIGH,
    NEW_PLAN_SETS_LOW,
    SECONDARY_SET_WEIGHT,
    SESSION_SETS_HIGH,
    WEEKLY_SETS_HIGH,
    WEEKLY_SETS_LOW,
)
from spotter.engine.types import VolumeDay


def weekly_sets_per_muscle(days: Sequence[VolumeDay]) -> dict[str, Decimal]:
    """Hard sets per muscle per week: 1 per set if primary, 0.5 if secondary.

    Each plan day runs once a week. Sorted by muscle name.
    """
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for day in days:
        for ex in day.exercises:
            for m in ex.primary:
                totals[m] += ex.sets
            for m in ex.secondary:
                totals[m] += ex.sets * SECONDARY_SET_WEIGHT
    return dict(sorted(totals.items()))


def _fmt(value: Decimal) -> str:
    return f"{value.normalize():f}"


def check(days: Sequence[VolumeDay], recent_history: bool) -> list[str]:
    """Warnings for primary muscles outside 8 to 20 weekly sets, sessions above 25 sets,
    and, for a plan with no comparable recent history, muscles outside 10 to 12."""
    warnings = []
    totals = weekly_sets_per_muscle(days)
    primary = {m for d in days for ex in d.exercises for m in ex.primary}
    for muscle in sorted(primary):
        n = totals[muscle]
        if n < WEEKLY_SETS_LOW:
            warnings.append(f"weekly_sets_low:{muscle}:{_fmt(n)}<{WEEKLY_SETS_LOW}")
        elif n > WEEKLY_SETS_HIGH:
            warnings.append(f"weekly_sets_high:{muscle}:{_fmt(n)}>{WEEKLY_SETS_HIGH}")
        if not recent_history and not NEW_PLAN_SETS_LOW <= n <= NEW_PLAN_SETS_HIGH:
            warnings.append(
                f"new_plan_sets:{muscle}:{_fmt(n)} outside {NEW_PLAN_SETS_LOW}-{NEW_PLAN_SETS_HIGH}"
            )
    for day in days:
        n_sets = sum(ex.sets for ex in day.exercises)
        if n_sets > SESSION_SETS_HIGH:
            warnings.append(f"session_sets_high:{day.label}:{n_sets}>{SESSION_SETS_HIGH}")
    return warnings
