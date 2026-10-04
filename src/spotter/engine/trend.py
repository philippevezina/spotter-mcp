"""Exercise history trend for `get_exercise_history`. Display only; no rule acts on it.

Metric: e1RM when every point has one, else top-set weight, else top-set reps
(bodyweight work). Compares the first and last points of the window.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from decimal import Decimal

from spotter.engine.rules import TREND_FLAT_FRACTION, TREND_MIN_POINTS
from spotter.engine.types import Trend, TrendDirection, TrendMetric, TrendPoint

_METRICS: tuple[tuple[TrendMetric, Callable[[TrendPoint], Decimal | int | None]], ...] = (
    ("e1rm", lambda p: p.best_e1rm_kg),
    ("top_set_weight", lambda p: p.top_set_weight_kg),
    ("top_set_reps", lambda p: p.top_set_reps),
)


def _direction(first: Decimal, change: Decimal) -> TrendDirection:
    if first == 0:
        return "up" if change > 0 else "flat"
    if abs(change / first) < TREND_FLAT_FRACTION:
        return "flat"
    return "up" if change > 0 else "down"


def trend(points: Sequence[TrendPoint]) -> Trend:
    """Points in any order. Needs 2 points that all carry the same metric."""
    ordered = sorted(points, key=lambda p: p.local_date)
    if len(ordered) >= TREND_MIN_POINTS:
        for metric, get in _METRICS:
            values = [get(p) for p in ordered]
            if any(v is None for v in values):
                continue
            first, last = Decimal(values[0] or 0), Decimal(values[-1] or 0)
            change = last - first
            return Trend(
                metric=metric,
                first=first,
                last=last,
                change=change,
                change_fraction=change / first if first else None,
                direction=_direction(first, change),
                points=len(ordered),
            )
    return Trend(None, None, None, None, None, "insufficient_data", len(ordered))
