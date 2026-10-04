"""Plain value types shared by engine modules. Weights in kg, as stored."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class SetPerformance:
    """One performed set. `weight_kg` is None for bodyweight work."""

    weight_kg: Decimal | None
    reps: int | None


@dataclass(frozen=True)
class ExerciseSummary:
    """One exercise in one session, over working sets only."""

    top_set_weight_kg: Decimal | None
    top_set_reps: int
    best_e1rm_kg: Decimal | None
    total_reps: int
    volume_kg: Decimal
    working_sets: int
