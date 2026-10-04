# /// script
# requires-python = ">=3.12"
# dependencies = ["garminconnect[workout]==0.3.17"]
# ///
"""Phase 0 check 3: push a weighted strength workout and schedule it for tomorrow.

    uv run spikes/push_test_workout.py             # dry run: print the JSON only
    uv run spikes/push_test_workout.py --push      # upload + schedule tomorrow
    uv run spikes/push_test_workout.py --update    # bench 185 -> 190 lb via update_workout, check schedule
    uv run spikes/push_test_workout.py --cleanup   # unschedule + delete

Exercise A uses a repeat group (same weight every set).
Exercise B uses individual steps (top set + back-offs, different weights).
Watch check: 185 / 225 / 205 lb (190 after --update), with the watch in statute units.
"""

from __future__ import annotations

import argparse
import json
from decimal import ROUND_HALF_UP, Decimal
from datetime import date, timedelta
from typing import Any

from _garmin import OUT_DIR, close_client, kg_to_lb, lb_to_kg, load_client, write_out
from garminconnect import exercises
from garminconnect.workout import (
    StrengthWorkout,
    WorkoutSegment,
    create_strength_exercise_step,
    create_strength_rest_step,
    create_strength_set,
)

PUSHED = OUT_DIR / "pushed.json"
BENCH = ("BENCH_PRESS", "BARBELL_BENCH_PRESS")
SQUAT = ("SQUAT", "BARBELL_BACK_SQUAT")


def check_exercise(category: str, name: str) -> None:
    known = {(e["category"], e["exercise"]) for e in exercises.EXERCISES}
    if (category, name) not in known:
        raise SystemExit(f"Unknown Garmin exercise: {category}/{name}")


def build(day: date, bench_lb: float) -> StrengthWorkout:
    for pair in (BENCH, SQUAT):
        check_exercise(*pair)

    order = 1
    steps: list[Any] = []

    # A: repeat group, 3x5 @ bench_lb, 120 s rest. Uses step orders n..n+2.
    steps.append(create_strength_set(BENCH[0], order, sets=3, reps=5, rest_seconds=120,
                                     exercise_name=BENCH[1], weight_kg=lb_to_kg(bench_lb, places=6)))
    order += 3

    # B: individual steps, 1x3 @ 225 then 2x5 @ 205.
    squat_sets = [(3, 225.0, 180), (5, 205.0, 120), (5, 205.0, None)]
    for reps, lb, rest in squat_sets:
        steps.append(create_strength_exercise_step(SQUAT[0], order, reps=reps,
                                                   exercise_name=SQUAT[1], weight_kg=lb_to_kg(lb, places=6)))
        order += 1
        if rest:
            steps.append(create_strength_rest_step(rest, order))
            order += 1

    sport = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5}
    return StrengthWorkout(
        workoutName=f"Spotter test W0 {day.isoformat()}",
        description=f"Phase 0 spike. Bench {bench_lb:g} lb.",
        estimatedDurationInSecs=1800,
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=sport, workoutSteps=steps)],
    )


def summarize(payload: dict[str, Any]) -> None:
    """Print each exercise step with its weight converted back to lb."""
    def walk(steps: list[dict[str, Any]], indent: str = "  ") -> None:
        for s in steps:
            if s.get("workoutSteps"):
                print(f"{indent}repeat x{s.get('numberOfIterations')} (order {s.get('stepOrder')})")
                walk(s["workoutSteps"], indent + "  ")
                continue
            kind = s.get("stepType", {}).get("stepTypeKey")
            if s.get("weightValue") is not None:
                # Spike heuristic: values > 1000 are the helper's grams, else app-style kg.
                kg = s["weightValue"] / 1000 if s["weightValue"] > 1000 else s["weightValue"]
                print(f"{indent}{s.get('stepOrder'):>2} {kind} {s.get('category')}/{s.get('exerciseName')} "
                      f"x{s.get('endConditionValue')} weightValue={s['weightValue']} "
                      f"({kg:.3f} kg = {kg_to_lb(kg)} lb) unit={s.get('weightUnit', {}).get('unitKey')}")
            else:
                print(f"{indent}{s.get('stepOrder'):>2} {kind} {s.get('endConditionValue')}")
    for seg in payload.get("workoutSegments", []):
        walk(seg["workoutSteps"])


def to_payload(workout: StrengthWorkout, encoding: str) -> dict[str, Any]:
    """Serialize, then rewrite weights. 'grams' keeps the library helper's output."""
    payload = workout.to_dict()
    if encoding == "kg":
        def walk(steps: list[dict[str, Any]]) -> None:
            for s in steps:
                walk(s.get("workoutSteps") or [])
                if s.get("weightValue") is not None:
                    s["weightValue"] = float(
                        Decimal(str(s["weightValue"])).scaleb(-3).quantize(Decimal("0.01"), ROUND_HALF_UP)
                    )
        for seg in payload["workoutSegments"]:
            walk(seg["workoutSteps"])
    return payload


def pick(d: dict[str, Any], *keys: str) -> Any:
    return next((d[k] for k in keys if d.get(k) is not None), None)


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--push", action="store_true")
    g.add_argument("--update", action="store_true")
    g.add_argument("--cleanup", action="store_true")
    ap.add_argument("--encoding", choices=["kg", "grams"], default="kg",
                    help="kg: app-style 2-decimal kg (default). grams: library helper output.")
    args = ap.parse_args()

    tomorrow = date.today() + timedelta(days=1)

    if not (args.push or args.update or args.cleanup):
        payload = to_payload(build(tomorrow, 185), args.encoding)
        summarize(payload)
        print(f"\nFull JSON: {write_out('workout_dry_run.json', payload)}")
        return

    garmin = load_client()
    try:
        if args.push:
            if PUSHED.exists():
                raise SystemExit(f"{PUSHED} exists. Run --cleanup first so we never duplicate.")
            up = garmin.upload_workout(to_payload(build(tomorrow, 185), args.encoding))
            workout_id = pick(up, "workoutId")
            sched = garmin.schedule_workout(workout_id, tomorrow.isoformat())
            write_out("upload_response.json", up)
            write_out("schedule_response.json", sched)
            schedule_id = pick(sched, "workoutScheduleId", "scheduledWorkoutId", "id")
            write_out("pushed.json", {"workout_id": workout_id, "schedule_id": schedule_id,
                                      "date": tomorrow.isoformat()})
            print(f"Uploaded workout {workout_id}, scheduled {tomorrow} (schedule id {schedule_id})")
            print(f"Schedule response keys: {sorted(sched)}")
            print(f"Description kept by Garmin: {up.get('description')!r}")
            summarize(up)

        elif args.update:
            pushed = json.loads(PUSHED.read_text())
            day = date.fromisoformat(pushed["date"])
            payload = to_payload(build(day, 190), args.encoding)
            garmin.update_workout(pushed["workout_id"], payload)
            stored = garmin.get_workout_by_id(pushed["workout_id"])
            print("Stored after update:")
            summarize(stored)
            cal = garmin.get_scheduled_workouts(day.year, day.month)
            write_out("scheduled_after_update.json", cal)
            still = json.dumps(cal).count(str(pushed["workout_id"]))
            print(f"Workout id appears {still} time(s) in {day:%Y-%m} schedule (expect >= 1)")

        else:
            pushed = json.loads(PUSHED.read_text())
            if pushed.get("schedule_id"):
                garmin.unschedule_workout(pushed["schedule_id"])
            garmin.delete_workout(pushed["workout_id"])
            PUSHED.unlink()
            print(f"Unscheduled and deleted workout {pushed['workout_id']}")
    finally:
        close_client(garmin)


if __name__ == "__main__":
    main()
