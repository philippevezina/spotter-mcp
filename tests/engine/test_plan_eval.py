"""Rule A.10: plan end criteria and evaluate_plan's recommendation."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from spotter.engine.plan_eval import end_criteria, evaluate, plan_week
from spotter.engine.types import EndCriteria, SessionFact, StallEvent

START = date(2026, 9, 7)
DEFAULT = EndCriteria(6, 2, Decimal("0.7"), 2)
STALL = StallEvent(START, "stall", 3)
WARNING = StallEvent(START, "stall_warning", 2)


def sessions(today: date, *statuses: str) -> list[SessionFact]:
    """One session per day, ending the day before `today`, oldest first."""
    n = len(statuses)
    return [SessionFact(today - timedelta(days=n - i), s) for i, s in enumerate(statuses)]


def run(today: date, **kw: Any) -> Any:
    args: dict[str, Any] = {
        "start_date": START,
        "weeks": 6,
        "planned_end_date": START + timedelta(days=41),
        "criteria": DEFAULT,
        "sessions": [],
        "main_lift_stalls": {},
        "today": today,
    }
    return evaluate(**{**args, **kw})


def test_end_criteria_defaults_and_overrides() -> None:
    assert end_criteria(None) == DEFAULT
    assert end_criteria({"max_weeks": 8, "min_adherence": 0.8}) == EndCriteria(
        8, 2, Decimal("0.8"), 2
    )


@pytest.mark.parametrize(
    ("start", "today", "week"),
    [
        (None, START, None),
        (START, START - timedelta(days=1), None),
        (START, START, 1),
        (START, START + timedelta(days=6), 1),
        (START, START + timedelta(days=7), 2),
    ],
)
def test_plan_week(start: date | None, today: date, week: int | None) -> None:
    assert plan_week(start, today) == week


def test_continue_with_good_adherence() -> None:
    today = START + timedelta(days=21)
    out = run(today, sessions=sessions(today, "completed", "completed", "skipped", "completed"))
    assert (out.recommendation, out.week, out.weeks) == ("continue", 4, 6)
    assert (out.adherence, out.sessions_due, out.sessions_completed) == (Decimal("0.75"), 4, 3)
    assert out.reasons == ()


def test_adjust_on_low_adherence() -> None:
    today = START + timedelta(days=21)
    out = run(today, sessions=sessions(today, "completed", "planned", "skipped", "pushed"))
    assert out.recommendation == "adjust"
    assert out.reasons == ("adherence:1/4<0.7",)


def test_moved_sessions_are_not_due_and_window_is_bounded() -> None:
    today = START + timedelta(days=21)
    old = SessionFact(today - timedelta(days=15), "skipped")  # before the 2-week window
    future = SessionFact(today, "planned")  # today is not due yet
    recent = sessions(today, "completed", "moved", "completed")
    out = run(today, sessions=[old, future, *recent])
    assert (out.adherence, out.sessions_due) == (Decimal(1), 2)


def test_no_sessions_due() -> None:
    out = run(START)
    assert (out.recommendation, out.adherence, out.reasons) == (
        "continue",
        None,
        ("adherence:no_sessions_due",),
    )


def test_end_when_weeks_done() -> None:
    today = START + timedelta(days=42)
    out = run(today, sessions=sessions(today, "completed"))
    assert out.recommendation == "end"
    assert out.reasons == ("weeks_done:6", "past_planned_end:2026-10-18")


def test_max_weeks_caps_a_longer_plan() -> None:
    today = START + timedelta(days=42)
    out = run(today, weeks=8, planned_end_date=None, sessions=sessions(today, "completed"))
    assert (out.recommendation, out.reasons) == ("end", ("weeks_done:6",))


def test_end_on_stalled_main_lifts_beats_adjust() -> None:
    today = START + timedelta(days=21)
    out = run(
        today,
        sessions=sessions(today, "skipped", "skipped"),
        main_lift_stalls={3: [WARNING, STALL], 1: [STALL], 2: [WARNING]},
    )
    assert (out.recommendation, out.reset_lifts) == ("end", (1, 3))
    assert out.reasons == ("stalled_main_lifts:2>=2",)


def test_one_reset_is_not_enough() -> None:
    today = START + timedelta(days=21)
    out = run(today, main_lift_stalls={1: [STALL]})
    assert (out.recommendation, out.reset_lifts) == ("continue", (1,))


def test_before_start() -> None:
    out = run(START - timedelta(days=3))
    assert (out.recommendation, out.week) == ("continue", None)
