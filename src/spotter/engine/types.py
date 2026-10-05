"""Plain value types shared by engine modules.

Weights are kg, as stored, except in progression, plan and volume types, which work
in lb because rules A.3 to A.11 round to lb increments. The tool layer converts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

ReadinessClass = Literal["green", "amber", "red"]
Sport = Literal["run", "ride"]
TrendMetric = Literal["e1rm", "top_set_weight", "top_set_reps"]
TrendDirection = Literal["up", "flat", "down", "insufficient_data"]
Model = Literal["double_progression", "linear", "rir_based"]
Role = Literal["main", "secondary", "accessory"]
LoadType = Literal["barbell", "dumbbell_each", "machine", "cable", "bodyweight", "bodyweight_plus"]
Judgement = Literal["hit", "miss", "hold_range", "short"]
ProposalStatus = Literal["ok", "needs_calibration"]
StallFlag = Literal["stall_warning", "stall"]
Recommendation = Literal["continue", "adjust", "end"]


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


# Progression (lb) -------------------------------------------------------------


@dataclass(frozen=True)
class LoadedSet:
    """One performed set in lb, in set order. `weight_lb` is None for bodyweight work."""

    weight_lb: Decimal | None
    reps: int


@dataclass(frozen=True)
class Prescription:
    """One plan exercise, as the rules read it.

    `lower_body`: the exercise's pattern is squat, hinge or lunge (linear step size,
    endurance interference). `increment_lb` is None when load cannot progress.
    """

    sets: int
    rep_min: int
    rep_max: int
    target_rir: int
    rest_s: int
    role: Role
    model: Model
    increment_lb: Decimal | None
    load_type: LoadType | None = None
    lower_body: bool = False


@dataclass(frozen=True)
class Exposure:
    """One session of one exercise. `rir` from session notes; `targets` per set once
    sessions are pushed (Phase 5); `deload` when it falls in a deload week."""

    local_date: date
    sets: tuple[LoadedSet, ...]
    rir: int | None = None
    targets: tuple[int, ...] | None = None
    deload: bool = False


@dataclass(frozen=True)
class ExerciseProposal:
    """Next prescription for one exercise. `target_reps` has one entry per set.

    `last_weight_lb` and `last_reps` describe the exposure it was computed from, so
    modifiers can fall back to it.
    """

    status: ProposalStatus
    sets: int
    target_reps: tuple[int, ...]
    weight_lb: Decimal | None
    target_rir: int
    rest_s: int
    rule_fired: str
    reasons: tuple[str, ...] = ()
    flags: tuple[StallFlag, ...] = ()
    modifiers: tuple[str, ...] = ()
    last_weight_lb: Decimal | None = None
    last_reps: tuple[int, ...] = ()


@dataclass(frozen=True)
class StallEvent:
    local_date: date
    flag: StallFlag
    misses: int


# Plan evaluation ----------------------------------------------------------------


@dataclass(frozen=True)
class EndCriteria:
    max_weeks: int
    stall_lifts: int
    min_adherence: Decimal
    adherence_window_weeks: int


@dataclass(frozen=True)
class SessionFact:
    scheduled_date: date
    status: str  # planned|pushed|completed|skipped|moved


@dataclass(frozen=True)
class PlanEvaluation:
    recommendation: Recommendation
    week: int | None  # None before the start date
    weeks: int
    adherence: Decimal | None  # None when no session was due in the window
    sessions_due: int
    sessions_completed: int
    reset_lifts: tuple[int, ...]
    reasons: tuple[str, ...]


# Volume (create_plan) ------------------------------------------------------------


@dataclass(frozen=True)
class VolumeExercise:
    sets: int
    primary: tuple[str, ...]
    secondary: tuple[str, ...] = ()


@dataclass(frozen=True)
class VolumeDay:
    label: str
    exercises: tuple[VolumeExercise, ...]
