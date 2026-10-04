"""Rule A.7: endurance interference, and weekly run and ride totals."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from spotter.engine.endurance import (
    hard_reasons,
    interference,
    is_lower_body_day,
    sport,
    week_start,
    weekly_totals,
)
from spotter.engine.types import EnduranceActivity, InterferenceFlag, SportTotals, WeekTotals

D = Decimal
PLANNED = date(2026, 10, 8)  # a Thursday


def act(
    activity_type: str = "running",
    local_date: date = PLANNED,
    duration_s: int | None = 1800,
    aerobic_te: str | None = "2.5",
    anaerobic_te: str | None = "0.5",
    activity_id: int = 1,
    distance_m: str | None = None,
    training_load: str | None = None,
) -> EnduranceActivity:
    return EnduranceActivity(
        activity_id=activity_id,
        local_date=local_date,
        activity_type=activity_type,
        duration_s=duration_s,
        distance_m=None if distance_m is None else D(distance_m),
        training_load=None if training_load is None else D(training_load),
        aerobic_te=None if aerobic_te is None else D(aerobic_te),
        anaerobic_te=None if anaerobic_te is None else D(anaerobic_te),
    )


@pytest.mark.parametrize(
    ("activity_type", "expected"),
    [
        ("running", "run"),
        ("trail_running", "run"),
        ("treadmill_running", "run"),
        ("cycling", "ride"),
        ("indoor_cycling", "ride"),
        ("virtual_ride", "ride"),
        ("strength_training", None),
        ("walking", None),
    ],
)
def test_sport(activity_type: str, expected: str | None) -> None:
    assert sport(activity_type) == expected


@pytest.mark.parametrize(
    ("activity", "expected"),
    [
        pytest.param(act(), [], id="easy run"),
        pytest.param(act(duration_s=75 * 60), ["run_duration_s:4500>=4500"], id="run 75 min"),
        pytest.param(act(duration_s=75 * 60 - 1), [], id="run just under 75 min"),
        pytest.param(act(aerobic_te="4.0"), ["run_aerobic_te:4.0>=4.0"], id="run aerobic 4.0"),
        pytest.param(act(aerobic_te="3.9"), [], id="run aerobic 3.9"),
        pytest.param(act(anaerobic_te="3.0"), ["run_anaerobic_te:3.0>=3.0"], id="run anaerobic"),
        pytest.param(act(anaerobic_te="2.9"), [], id="run anaerobic 2.9"),
        pytest.param(
            act(duration_s=None, aerobic_te=None, anaerobic_te=None), [], id="run no data"
        ),
        pytest.param(
            act("cycling", duration_s=120 * 60), ["ride_duration_s:7200>=7200"], id="ride 2 h"
        ),
        pytest.param(act("cycling", duration_s=119 * 60), [], id="ride under 2 h"),
        pytest.param(
            act("cycling", aerobic_te="4.2"), ["ride_aerobic_te:4.2>=4.0"], id="ride aerobic"
        ),
        pytest.param(act("cycling", anaerobic_te="3.5"), [], id="ride anaerobic ignored"),
        pytest.param(act("strength_training", duration_s=9000), [], id="not endurance"),
        pytest.param(
            act(duration_s=6000, aerobic_te="4.5", anaerobic_te="3.1"),
            ["run_duration_s:6000>=4500", "run_aerobic_te:4.5>=4.0", "run_anaerobic_te:3.1>=3.0"],
            id="all run reasons",
        ),
    ],
)
def test_hard_reasons(activity: EnduranceActivity, expected: list[str]) -> None:
    assert hard_reasons(activity) == expected


@pytest.mark.parametrize(
    ("patterns", "expected"),
    [
        (["squat", "push_h"], True),
        (["hinge"], True),
        (["lunge"], True),
        (["push_h", "pull_v", None], False),
        ([], False),
    ],
)
def test_is_lower_body_day(patterns: list[str | None], expected: bool) -> None:
    assert is_lower_body_day(patterns) == expected


def test_interference_window_is_planned_date_and_two_before() -> None:
    hard = {"duration_s": 5400}
    acts = [
        act(local_date=date(2026, 10, 5), activity_id=1, **hard),  # 3 days before: outside
        act(local_date=date(2026, 10, 6), activity_id=2, **hard),
        act(local_date=date(2026, 10, 7), activity_id=3, **hard),
        act(local_date=PLANNED, activity_id=4, **hard),  # same day counts
        act(local_date=date(2026, 10, 9), activity_id=5, **hard),  # after: outside
    ]
    flags = interference(PLANNED, lower_body=True, activities=acts)
    assert [f.activity_id for f in flags] == [2, 3, 4]


def test_interference_flag_contents_and_order() -> None:
    acts = [
        act("cycling", local_date=PLANNED, activity_id=9, aerobic_te="4.1"),
        act(local_date=date(2026, 10, 7), activity_id=8, duration_s=4800),
        act(local_date=date(2026, 10, 7), activity_id=7),  # easy: no flag
        act("strength_training", local_date=PLANNED, activity_id=6, duration_s=9000),
    ]
    assert interference(PLANNED, lower_body=True, activities=acts) == [
        InterferenceFlag(8, date(2026, 10, 7), "run", ("run_duration_s:4800>=4500",)),
        InterferenceFlag(9, PLANNED, "ride", ("ride_aerobic_te:4.1>=4.0",)),
    ]


def test_upper_body_day_is_never_flagged() -> None:
    acts = [act(duration_s=9000)]
    assert interference(PLANNED, lower_body=False, activities=acts) == []


def test_week_start_is_monday() -> None:
    assert week_start(date(2026, 10, 5)) == date(2026, 10, 5)  # Monday
    assert week_start(date(2026, 10, 11)) == date(2026, 10, 5)  # Sunday


def test_weekly_totals() -> None:
    acts = [
        act(
            local_date=date(2026, 10, 11),
            duration_s=3600,
            distance_m="10000.0",
            training_load="80.5",
        ),
        act(local_date=date(2026, 10, 6), duration_s=1800, distance_m=None, training_load="40"),
        act(
            "cycling",
            local_date=date(2026, 10, 7),
            duration_s=None,
            distance_m="30000",
            training_load=None,
        ),
        act(local_date=date(2026, 9, 29), duration_s=2400, distance_m="6000", training_load="50"),
        act("strength_training", local_date=date(2026, 9, 22)),  # ignored, no week row
    ]
    zero = SportTotals(0, 0, D(0), D(0))
    assert weekly_totals(acts) == [
        WeekTotals(date(2026, 9, 28), SportTotals(1, 2400, D(6000), D(50)), zero),
        WeekTotals(
            date(2026, 10, 5),
            SportTotals(2, 5400, D("10000.0"), D("120.5")),
            SportTotals(1, 0, D(30000), D(0)),
        ),
    ]
