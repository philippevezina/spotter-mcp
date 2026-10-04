"""Working sets and per-exercise session summary (coaching-rules.md A.1, A.2)."""

from __future__ import annotations

from collections.abc import Iterable
from decimal import Decimal

from spotter.engine.e1rm import best_e1rm
from spotter.engine.rules import WARMUP_FRACTION
from spotter.engine.types import ExerciseSummary, SetPerformance


def _load(s: SetPerformance) -> Decimal:
    return s.weight_kg or Decimal(0)


def _reps(s: SetPerformance) -> int:
    return s.reps or 0


def working_sets(sets: Iterable[SetPerformance]) -> list[SetPerformance]:
    """Sets with reps at or above 60 % of the session top set weight.

    Sets with 0 or no reps are dropped first (the watch logs them as ACTIVE).
    With no load on any set (bodyweight), every set with reps is a working set.
    """
    done = [s for s in sets if _reps(s) > 0]
    top = max((_load(s) for s in done), default=Decimal(0))
    if top == 0:
        return done
    threshold = top * WARMUP_FRACTION
    return [s for s in done if _load(s) >= threshold]


def summarize(sets: Iterable[SetPerformance], e1rm_eligible: bool) -> ExerciseSummary | None:
    """Summary over working sets. None when there is no working set (not an exposure).

    Top set: heaviest, then most reps. e1RM only for eligible exercises.
    """
    work = working_sets(sets)
    if not work:
        return None
    top = max(work, key=lambda s: (_load(s), _reps(s)))
    return ExerciseSummary(
        top_set_weight_kg=top.weight_kg,
        top_set_reps=_reps(top),
        best_e1rm_kg=best_e1rm(work) if e1rm_eligible else None,
        total_reps=sum(_reps(s) for s in work),
        volume_kg=sum((_load(s) * _reps(s) for s in work), Decimal(0)),
        working_sets=len(work),
    )
