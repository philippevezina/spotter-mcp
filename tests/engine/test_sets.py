"""Rule A.1 (working sets) and the per-exercise session summary."""

from __future__ import annotations

from decimal import Decimal

import pytest

from spotter.engine.e1rm import epley
from spotter.engine.sets import summarize, working_sets
from spotter.engine.types import ExerciseSummary, SetPerformance

D = Decimal


def S(weight: int | str | None, reps: int | None) -> SetPerformance:
    return SetPerformance(None if weight is None else D(weight), reps)


@pytest.mark.parametrize(
    ("sets", "expected"),
    [
        pytest.param([], [], id="no sets"),
        pytest.param([S(100, 0), S(100, None)], [], id="only empty sets"),
        pytest.param(
            [S(40, 10), S(60, 5), S(100, 5)], [S(60, 5), S(100, 5)], id="60 % boundary kept"
        ),
        pytest.param([S("59.99", 5), S(100, 5)], [S(100, 5)], id="just below 60 % dropped"),
        pytest.param([S(100, 0), S(50, 8)], [S(50, 8)], id="0-rep set ignored for top"),
        pytest.param([S(None, 8), S(None, 6)], [S(None, 8), S(None, 6)], id="bodyweight"),
        pytest.param([S(None, 8), S(20, 6)], [S(20, 6)], id="added load beats bodyweight"),
    ],
)
def test_working_sets(sets: list[SetPerformance], expected: list[SetPerformance]) -> None:
    assert working_sets(sets) == expected


def test_summarize_none_without_working_sets() -> None:
    assert summarize([S(100, 0)], e1rm_eligible=True) is None


def test_summarize_ignores_warmups() -> None:
    sets = [S(40, 10), S(100, 5), S(100, 5), S(100, 4)]
    assert summarize(sets, e1rm_eligible=True) == ExerciseSummary(
        top_set_weight_kg=D(100),
        top_set_reps=5,
        best_e1rm_kg=epley(D(100), 5),
        total_reps=14,
        volume_kg=D(1400),
        working_sets=3,
    )


def test_summarize_not_eligible_has_no_e1rm() -> None:
    summary = summarize([S(100, 5)], e1rm_eligible=False)
    assert summary is not None and summary.best_e1rm_kg is None


def test_top_set_tie_breaks_on_reps() -> None:
    summary = summarize([S(100, 3), S(100, 6), S(90, 8)], e1rm_eligible=False)
    assert summary is not None
    assert (summary.top_set_weight_kg, summary.top_set_reps) == (D(100), 6)


def test_summarize_bodyweight() -> None:
    assert summarize([S(None, 8), S(None, 6)], e1rm_eligible=True) == ExerciseSummary(
        top_set_weight_kg=None,
        top_set_reps=8,
        best_e1rm_kg=None,
        total_reps=14,
        volume_kg=D(0),
        working_sets=2,
    )
