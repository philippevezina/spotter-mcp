"""Plain value types shared by engine modules. Weights in kg, as stored."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

ReadinessClass = Literal["green", "amber", "red"]
Sport = Literal["run", "ride"]
TrendMetric = Literal["e1rm", "top_set_weight", "top_set_reps"]
TrendDirection = Literal["up", "flat", "down", "insufficient_data"]


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


@dataclass(frozen=True)
class DailyReadinessInput:
    """The daily_metrics fields rule A.5 reads. None means Garmin had no value."""

    local_date: date
    hrv_status: str | None = None
    hrv_overnight_ms: int | None = None
    hrv_baseline_low: int | None = None
    training_readiness: int | None = None
    sleep_score: int | None = None
    body_battery_max: int | None = None


@dataclass(frozen=True)
class ReadinessDay:
    local_date: date
    readiness_class: ReadinessClass
    reasons: tuple[str, ...]
    has_data: bool


@dataclass(frozen=True)
class ReadinessWindow:
    days: int
    green: int
    amber: int
    red: int
    no_data: int


@dataclass(frozen=True)
class EnduranceActivity:
    """One run, ride or other activity, from `activities`."""

    activity_id: int
    local_date: date
    activity_type: str
    duration_s: int | None = None
    distance_m: Decimal | None = None
    training_load: Decimal | None = None
    aerobic_te: Decimal | None = None
    anaerobic_te: Decimal | None = None


@dataclass(frozen=True)
class InterferenceFlag:
    activity_id: int
    local_date: date
    sport: Sport
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class SportTotals:
    sessions: int
    duration_s: int
    distance_m: Decimal
    training_load: Decimal


@dataclass(frozen=True)
class WeekTotals:
    week_start: date  # Monday, athlete's local date
    run: SportTotals
    ride: SportTotals


@dataclass(frozen=True)
class TrendPoint:
    """One exposure from exercise_session_stats. Weights in kg."""

    local_date: date
    best_e1rm_kg: Decimal | None
    top_set_weight_kg: Decimal | None
    top_set_reps: int | None


@dataclass(frozen=True)
class Trend:
    metric: TrendMetric | None  # None when fewer than 2 comparable points
    first: Decimal | None
    last: Decimal | None
    change: Decimal | None
    change_fraction: Decimal | None
    direction: TrendDirection
    points: int
