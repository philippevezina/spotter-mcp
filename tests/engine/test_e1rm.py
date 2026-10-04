"""Rule A.2: Epley, sets of 1 to 10 reps only, best set of the session."""

from __future__ import annotations

from decimal import Decimal

import pytest

from spotter.engine.e1rm import best_e1rm, epley
from spotter.engine.types import SetPerformance

D = Decimal


@pytest.mark.parametrize(
    ("weight", "reps", "expected"),
    [
        (D(100), 1, D(100) * (1 + D(1) / 30)),
        (D(100), 5, D(100) * (1 + D(5) / 30)),
        (D(90), 10, D(120)),
        (D(30), 0, D(30)),
    ],
)
def test_epley(weight: Decimal, reps: int, expected: Decimal) -> None:
    assert epley(weight, reps) == expected


def S(weight: int | None, reps: int | None) -> SetPerformance:
    return SetPerformance(None if weight is None else D(weight), reps)


@pytest.mark.parametrize(
    ("sets", "expected"),
    [
        pytest.param([], None, id="no sets"),
        pytest.param([S(100, 5), S(90, 10)], D(120), id="best set wins"),
        pytest.param([S(100, 1)], epley(D(100), 1), id="1 rep counts"),
        pytest.param([S(90, 10)], D(120), id="10 reps counts"),
        pytest.param([S(100, 11)], None, id="11 reps excluded"),
        pytest.param([S(100, 0)], None, id="0 reps excluded"),
        pytest.param([S(None, 8)], None, id="bodyweight excluded"),
        pytest.param([S(0, 8)], None, id="zero load excluded"),
        pytest.param([S(100, None)], None, id="missing reps excluded"),
        pytest.param([S(100, 12), S(80, 3)], epley(D(80), 3), id="only eligible set counts"),
    ],
)
def test_best_e1rm(sets: list[SetPerformance], expected: Decimal | None) -> None:
    assert best_e1rm(sets) == expected
