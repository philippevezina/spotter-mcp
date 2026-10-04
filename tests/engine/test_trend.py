"""Exercise history trend (display only)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from spotter.engine.trend import trend
from spotter.engine.types import Trend, TrendPoint

D = Decimal


def P(
    day: int, e1rm: str | None = None, top: str | None = None, reps: int | None = 5
) -> TrendPoint:
    return TrendPoint(
        local_date=date(2026, 9, day),
        best_e1rm_kg=None if e1rm is None else D(e1rm),
        top_set_weight_kg=None if top is None else D(top),
        top_set_reps=reps,
    )


def test_e1rm_up_points_in_any_order() -> None:
    result = trend([P(20, "110", "95"), P(1, "100", "85"), P(10, "105", "90")])
    assert result == Trend("e1rm", D(100), D(110), D(10), D("0.1"), "up", 3)


def test_falls_back_to_top_set_when_any_e1rm_missing() -> None:
    result = trend([P(1, "100", "80"), P(2, None, "76")])
    assert (result.metric, result.change, result.direction) == ("top_set_weight", D(-4), "down")


def test_falls_back_to_reps_for_bodyweight() -> None:
    result = trend([P(1, reps=8), P(2, reps=10)])
    assert (result.metric, result.first, result.last, result.direction) == (
        "top_set_reps",
        D(8),
        D(10),
        "up",
    )


@pytest.mark.parametrize(
    ("last", "direction"),
    [("100.99", "flat"), ("99.01", "flat"), ("101", "up"), ("99", "down")],
)
def test_flat_band_is_one_percent(last: str, direction: str) -> None:
    assert trend([P(1, "100"), P(2, last)]).direction == direction


def test_zero_first_value() -> None:
    assert trend([P(1, reps=0), P(2, reps=3)]).direction == "up"
    flat = trend([P(1, reps=0), P(2, reps=0)])
    assert (flat.direction, flat.change_fraction) == ("flat", None)


@pytest.mark.parametrize(
    "points",
    [
        pytest.param([], id="no points"),
        pytest.param([P(1, "100", "90")], id="one point"),
        pytest.param([P(1, reps=None), P(2, reps=None)], id="no comparable metric"),
    ],
)
def test_insufficient_data(points: list[TrendPoint]) -> None:
    result = trend(points)
    assert (result.metric, result.direction, result.points) == (
        None,
        "insufficient_data",
        len(points),
    )
