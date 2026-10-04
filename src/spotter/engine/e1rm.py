"""Estimated 1RM (coaching-rules.md A.2)."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from spotter.engine.rules import E1RM_MAX_REPS, E1RM_MIN_REPS, EPLEY_REP_DIVISOR
from spotter.engine.types import SetPerformance


def epley(weight: Decimal, reps: int) -> Decimal:
    """e1rm = weight x (1 + reps / 30). Unit-free."""
    return weight * (1 + Decimal(reps) / EPLEY_REP_DIVISOR)


def best_e1rm(sets: Iterable[SetPerformance]) -> Decimal | None:
    """Best e1RM over loaded sets of 1 to 10 reps. None when no set qualifies.

    Callers pass working sets of an e1rm-eligible exercise only.
    """
    estimates = [
        epley(s.weight_kg, s.reps)
        for s in sets
        if s.weight_kg and s.reps is not None and E1RM_MIN_REPS <= s.reps <= E1RM_MAX_REPS
    ]
    return max(estimates, default=None)
