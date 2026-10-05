"""Domain prescription -> Garmin strength workout JSON (SPEC 8.4).

Builds the dict itself, in the shape Garmin accepted in Phase 0, instead of using
`garminconnect.workout`: only `spotter.garmin.client` imports the library, and its
helpers send weights in grams, which the watch does not display (decisions.md).
Step weights are kg with 2 decimals, from `spotter.units.garmin_step_kg`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from spotter.units import garmin_step_kg

SPORT = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5}
NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}
KILOGRAM = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
INTERVAL = {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3}
REST = {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5}
REPEAT = {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6}
END_REPS = {
    "conditionTypeId": 10,
    "conditionTypeKey": "reps",
    "displayOrder": 10,
    "displayable": True,
}
END_TIME = {
    "conditionTypeId": 2,
    "conditionTypeKey": "time",
    "displayOrder": 2,
    "displayable": True,
}
END_ITERATIONS = {
    "conditionTypeId": 7,
    "conditionTypeKey": "iterations",
    "displayOrder": 7,
    "displayable": False,
}
SECONDS_PER_SET = 45  # rough time under the bar, only for Garmin's duration estimate


@dataclass(frozen=True)
class WorkoutSet:
    reps: int
    weight_lb: Decimal | None  # None for bodyweight; dumbbells per hand


@dataclass(frozen=True)
class WorkoutExercise:
    garmin_category: str
    garmin_name: str  # '' means category only
    sets: tuple[WorkoutSet, ...]
    rest_s: int | None
    increment_lb: Decimal | None


@dataclass(frozen=True)
class Workout:
    name: str
    description: str
    exercises: tuple[WorkoutExercise, ...]


def workout_name(label: str, week_no: int, day: date) -> str:
    return f"{label} W{week_no} {day.isoformat()}"


def _exercise_step(ex: WorkoutExercise, s: WorkoutSet, order: int) -> dict[str, Any]:
    step: dict[str, Any] = {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": INTERVAL,
        "endCondition": END_REPS,
        "endConditionValue": float(s.reps),
        "targetType": NO_TARGET,
        "category": ex.garmin_category,
    }
    if ex.garmin_name:
        step["exerciseName"] = ex.garmin_name
    if s.weight_lb is not None:
        if ex.increment_lb is None:
            raise ValueError(f"{ex.garmin_category}/{ex.garmin_name}: weight without increment")
        step["weightValue"] = float(garmin_step_kg(s.weight_lb, ex.increment_lb))
        step["weightUnit"] = KILOGRAM
    return step


def _rest_step(rest_s: int, order: int) -> dict[str, Any]:
    return {
        "type": "ExecutableStepDTO",
        "stepOrder": order,
        "stepType": REST,
        "endCondition": END_TIME,
        "endConditionValue": float(rest_s),
        "targetType": NO_TARGET,
    }


def _uniform(ex: WorkoutExercise) -> bool:
    return len(ex.sets) > 1 and len(set(ex.sets)) == 1


def build_workout(workout: Workout) -> dict[str, Any]:
    """The JSON for `upload_workout` and `update_workout`.

    Identical sets become one repeat group (its rest follows every set). Different
    sets become exercise + rest step pairs, so each set keeps its own weight. Rest
    follows every set except the last one of the workout. Step orders are unique.
    """
    if not workout.exercises or any(not ex.sets for ex in workout.exercises):
        raise ValueError("a workout needs at least one exercise, each with a set")
    steps: list[dict[str, Any]] = []
    order = 1
    duration = 0
    last_ex = len(workout.exercises) - 1
    for i, ex in enumerate(workout.exercises):
        rest = ex.rest_s or 0
        duration += len(ex.sets) * (SECONDS_PER_SET + rest)
        if _uniform(ex):
            inner = [_exercise_step(ex, ex.sets[0], order + 1)]
            if rest:
                inner.append(_rest_step(rest, order + 2))
            steps.append(
                {
                    "type": "RepeatGroupDTO",
                    "stepOrder": order,
                    "stepType": REPEAT,
                    "numberOfIterations": len(ex.sets),
                    "smartRepeat": False,
                    "endCondition": END_ITERATIONS,
                    "endConditionValue": float(len(ex.sets)),
                    "workoutSteps": inner,
                }
            )
            order += 3
            continue
        for j, s in enumerate(ex.sets):
            steps.append(_exercise_step(ex, s, order))
            order += 1
            last_set = i == last_ex and j == len(ex.sets) - 1
            if rest and not last_set:
                steps.append(_rest_step(rest, order))
                order += 1
    return {
        "workoutName": workout.name,
        "description": workout.description,
        "sportType": SPORT,
        "estimatedDurationInSecs": duration,
        "workoutSegments": [{"segmentOrder": 1, "sportType": SPORT, "workoutSteps": steps}],
    }
