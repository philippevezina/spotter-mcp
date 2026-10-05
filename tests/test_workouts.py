"""Workout builder: the JSON sent to Garmin for known prescriptions (SPEC 14)."""

from __future__ import annotations

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from spotter.garmin.workouts import (
    Workout,
    WorkoutExercise,
    WorkoutSet,
    build_workout,
    workout_name,
)

SNAPSHOT = Path(__file__).parent / "snapshots" / "workout_upper.json"
FIVE = Decimal(5)


def _sets(*pairs: tuple[int, int | None]) -> tuple[WorkoutSet, ...]:
    return tuple(WorkoutSet(r, None if w is None else Decimal(w)) for r, w in pairs)


BENCH = WorkoutExercise("BENCH_PRESS", "BARBELL_BENCH_PRESS", _sets(*[(5, 185)] * 3), 120, FIVE)
SQUAT = WorkoutExercise(
    "SQUAT", "BARBELL_BACK_SQUAT", _sets((3, 225), (5, 205), (5, 205)), 180, FIVE
)
PULLUP = WorkoutExercise("PULL_UP", "PULL_UP", _sets((8, None), (8, None), (6, None)), 90, None)
UPPER = Workout(
    name=workout_name("Upper", 4, date(2026, 10, 6)),
    description="Bench +5 lb.",
    exercises=(BENCH, SQUAT, PULLUP),
)


def _exercise_steps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(steps: list[dict[str, Any]]) -> None:
        for s in steps:
            walk(s.get("workoutSteps") or [])
            out.append(s)

    walk(payload["workoutSegments"][0]["workoutSteps"])
    return out


def test_snapshot() -> None:
    payload = build_workout(UPPER)
    if os.environ.get("UPDATE_SNAPSHOTS"):
        SNAPSHOT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    assert payload == json.loads(SNAPSHOT.read_text())


def test_name_and_description() -> None:
    payload = build_workout(UPPER)
    assert payload["workoutName"] == "Upper W4 2026-10-06"
    assert payload["description"] == "Bench +5 lb."


def test_identical_sets_form_one_repeat_group() -> None:
    group = build_workout(UPPER)["workoutSegments"][0]["workoutSteps"][0]
    assert group["type"] == "RepeatGroupDTO"
    assert group["numberOfIterations"] == 3
    assert [s["stepType"]["stepTypeKey"] for s in group["workoutSteps"]] == ["interval", "rest"]


def test_weights_are_two_decimal_kg() -> None:
    weights = [s.get("weightValue") for s in _exercise_steps(build_workout(UPPER))]
    # 185 lb -> 83.91 kg (decisions.md), 225 -> 102.06, 205 -> 92.99; pull-ups carry none.
    assert [w for w in weights if w is not None] == [83.91, 102.06, 92.99, 92.99]
    pullups = [s for s in _exercise_steps(build_workout(UPPER)) if s.get("category") == "PULL_UP"]
    assert pullups and all("weightValue" not in s and "weightUnit" not in s for s in pullups)


def test_step_orders_are_unique_and_no_rest_ends_the_workout() -> None:
    steps = _exercise_steps(build_workout(UPPER))
    orders = [s["stepOrder"] for s in steps]
    assert len(orders) == len(set(orders))
    top = build_workout(UPPER)["workoutSegments"][0]["workoutSteps"]
    assert top[-1]["stepType"]["stepTypeKey"] == "interval"


def test_category_only_exercise_has_no_name() -> None:
    ex = WorkoutExercise("PLANK", "", _sets((1, None)), None, None)
    step = build_workout(Workout("Core W1 2026-10-06", "", (ex,)))["workoutSegments"][0][
        "workoutSteps"
    ][0]
    assert step["category"] == "PLANK" and "exerciseName" not in step


def test_repeat_group_without_rest() -> None:
    ex = WorkoutExercise("PLANK", "PLANK", _sets((1, None), (1, None)), None, None)
    group = build_workout(Workout("x", "", (ex,)))["workoutSegments"][0]["workoutSteps"][0]
    assert [s["stepType"]["stepTypeKey"] for s in group["workoutSteps"]] == ["interval"]


def test_weight_without_increment_is_rejected() -> None:
    ex = WorkoutExercise("PULL_UP", "PULL_UP", _sets((8, 25)), 90, None)
    with pytest.raises(ValueError, match="increment"):
        build_workout(Workout("x", "", (ex,)))


@pytest.mark.parametrize("exercises", [(), (WorkoutExercise("SQUAT", "", (), 60, FIVE),)])
def test_empty_workout_is_rejected(exercises: tuple[WorkoutExercise, ...]) -> None:
    with pytest.raises(ValueError, match="at least one"):
        build_workout(Workout("x", "", exercises))
