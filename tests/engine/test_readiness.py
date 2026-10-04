"""Rule A.5: readiness classification."""

from __future__ import annotations

from datetime import date

import pytest

from spotter.engine.readiness import FIELDS, classify, summarize_window
from spotter.engine.types import DailyReadinessInput, ReadinessDay, ReadinessWindow

DAY = date(2026, 10, 4)
# Every input present and comfortably green.
GOOD = {
    "hrv_status": "BALANCED",
    "hrv_overnight_ms": 50,
    "hrv_baseline_low": 45,
    "training_readiness": 70,
    "sleep_score": 80,
    "body_battery_max": 85,
}


def day(**overrides: object) -> DailyReadinessInput:
    return DailyReadinessInput(local_date=DAY, **{**GOOD, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "cls", "reasons"),
    [
        pytest.param({}, "green", (), id="all good"),
        # red
        pytest.param({"hrv_status": "LOW"}, "red", ("hrv_status:LOW",), id="hrv LOW"),
        pytest.param({"hrv_status": "poor"}, "red", ("hrv_status:POOR",), id="hrv poor, any case"),
        pytest.param(
            {"training_readiness": 24},
            "red",
            ("training_readiness:24<25",),
            id="readiness 24 red",
        ),
        pytest.param(
            {"sleep_score": 49, "body_battery_max": 39},
            "red",
            ("sleep_score:49<50+body_battery_max:39<40", "sleep_score:49<60"),
            id="poor sleep and low battery red",
        ),
        # amber
        pytest.param(
            {"training_readiness": 25},
            "amber",
            ("training_readiness:25<50",),
            id="readiness 25 amber",
        ),
        pytest.param(
            {"training_readiness": 49},
            "amber",
            ("training_readiness:49<50",),
            id="readiness 49 amber",
        ),
        pytest.param({"training_readiness": 50}, "green", (), id="readiness 50 green"),
        pytest.param(
            {"sleep_score": 49, "body_battery_max": 40},
            "amber",
            ("sleep_score:49<60",),
            id="poor sleep, battery at 40, amber only",
        ),
        pytest.param({"sleep_score": 59}, "amber", ("sleep_score:59<60",), id="sleep 59 amber"),
        pytest.param({"sleep_score": 60}, "green", (), id="sleep 60 green"),
        pytest.param(
            {"hrv_overnight_ms": 44},
            "amber",
            ("hrv_below_baseline:44<45",),
            id="hrv below baseline",
        ),
        pytest.param({"hrv_overnight_ms": 45}, "green", (), id="hrv at baseline low"),
        pytest.param({"hrv_status": "UNBALANCED"}, "green", (), id="unbalanced is not red"),
        # red and amber together: red first
        pytest.param(
            {"hrv_status": "LOW", "sleep_score": 55},
            "red",
            ("hrv_status:LOW", "sleep_score:55<60"),
            id="red plus amber reasons",
        ),
    ],
)
def test_classify(overrides: dict[str, object], cls: str, reasons: tuple[str, ...]) -> None:
    result = classify(day(**overrides))
    assert result == ReadinessDay(DAY, cls, reasons, has_data=True)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("overrides", "cls", "reasons"),
    [
        pytest.param(
            {"sleep_score": 45, "body_battery_max": None},
            "amber",
            ("sleep_score:45<60", "unknown:body_battery_max"),
            id="poor sleep, no battery: never red",
        ),
        pytest.param(
            {"sleep_score": None, "body_battery_max": 10},
            "green",
            ("unknown:sleep_score",),
            id="low battery, no sleep: never red",
        ),
        pytest.param(
            {"hrv_baseline_low": None, "hrv_overnight_ms": 10},
            "green",
            ("unknown:hrv_baseline_low",),
            id="no baseline: no hrv amber",
        ),
        pytest.param(
            {"hrv_overnight_ms": None},
            "green",
            ("unknown:hrv_overnight_ms",),
            id="no overnight hrv",
        ),
        pytest.param(
            {"hrv_status": None, "training_readiness": None},
            "green",
            ("unknown:hrv_status", "unknown:training_readiness"),
            id="missing red inputs",
        ),
    ],
)
def test_missing_data_never_red_alone(
    overrides: dict[str, object], cls: str, reasons: tuple[str, ...]
) -> None:
    result = classify(day(**overrides))
    assert (result.readiness_class, result.reasons, result.has_data) == (cls, reasons, True)


def test_no_data_at_all() -> None:
    result = classify(DailyReadinessInput(local_date=DAY))
    assert result.readiness_class == "green"
    assert result.reasons == tuple(f"unknown:{f}" for f in FIELDS)
    assert result.has_data is False


def test_summarize_window_counts_classes_and_empty_days() -> None:
    days = [
        classify(day()),
        classify(day(sleep_score=55)),
        classify(day(hrv_status="LOW")),
        classify(day(training_readiness=10)),
        classify(DailyReadinessInput(local_date=DAY)),
    ]
    assert summarize_window(days) == ReadinessWindow(days=5, green=1, amber=1, red=2, no_data=1)


def test_summarize_empty_window() -> None:
    assert summarize_window([]) == ReadinessWindow(days=0, green=0, amber=0, red=0, no_data=0)
