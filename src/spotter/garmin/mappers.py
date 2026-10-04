"""Garmin JSON -> row dicts for `activities` and `performed_sets`. Pure, no I/O.

Raw Garmin JSON stops here: only `activities.raw_json` keeps the summary, and
it is never returned by tools or logged.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from spotter.units import grams_to_kg

STRENGTH_TYPE = "strength_training"
ACTIVE_SET = "ACTIVE"


def _dec(value: Any, places: str) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal(places))


def _int(value: Any) -> int | None:
    return None if value is None else round(value)


def _utc(value: str) -> datetime:
    """Garmin GMT strings: '2025-10-05 18:32:25' (activities), '2025-10-05T18:32:25.0' (sets)."""
    return datetime.fromisoformat(value.replace(" ", "T")).replace(tzinfo=UTC)


def activity_type(summary: dict[str, Any]) -> str:
    type_key: str = summary["activityType"]["typeKey"]
    return type_key


def is_strength(summary: dict[str, Any]) -> bool:
    return activity_type(summary) == STRENGTH_TYPE


def workout_id(summary: dict[str, Any]) -> int | None:
    """The pushed workout this activity was started from, if any (Phase 5 linking)."""
    return _int(summary.get("workoutId"))


def map_activity(summary: dict[str, Any], tz: ZoneInfo) -> dict[str, Any]:
    """One entry of `get_activities_by_date`. `local_date` is the start in the athlete's tz."""
    start = _utc(summary["startTimeGMT"])
    return {
        "garmin_activity_id": int(summary["activityId"]),
        "type": activity_type(summary),
        "start_time": start,
        "local_date": start.astimezone(tz).date(),
        "duration_s": _int(summary.get("duration")),
        "distance_m": _dec(summary.get("distance"), "0.1"),
        "elevation_gain_m": _dec(summary.get("elevationGain"), "0.1"),
        "avg_hr": _int(summary.get("averageHR")),
        "max_hr": _int(summary.get("maxHR")),
        "training_load": _dec(summary.get("activityTrainingLoad"), "0.1"),
        "aerobic_te": _dec(summary.get("aerobicTrainingEffect"), "0.1"),
        "anaerobic_te": _dec(summary.get("anaerobicTrainingEffect"), "0.1"),
        # Garmin's own scales, stored as given: RPE 10-100 (10 per point), feel 0-100.
        "perceived_effort": _int(summary.get("directWorkoutRpe")),
        "feel": _int(summary.get("directWorkoutFeel")),
        "raw_json": summary,
    }


def _best_candidate(candidates: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """The watch lists exercise guesses with a probability. Take the most likely one."""
    if not candidates:
        return None, None
    best = max(candidates, key=lambda c: c.get("probability") or 0)
    return best.get("category"), best.get("name")


def map_exercise_sets(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """ACTIVE sets of `get_activity_exercise_sets`. REST sets are dropped.

    `set_index` is Garmin's `messageIndex`. 0-rep sets are kept; stats ignore them.
    `exercise_id` is resolved later, against the `exercises` table.
    """
    rows = []
    for s in payload.get("exerciseSets") or []:
        if s.get("setType") != ACTIVE_SET:
            continue
        category, name = _best_candidate(s.get("exercises") or [])
        weight = s.get("weight")
        rows.append(
            {
                "set_index": int(s["messageIndex"]),
                "garmin_category": category,
                "garmin_name": name,
                "reps": s.get("repetitionCount"),
                "weight_kg": None if weight is None else grams_to_kg(weight),
                "duration_s": _dec(s.get("duration"), "0.1"),
                "start_time": _utc(s["startTime"]) if s.get("startTime") else None,
            }
        )
    return rows
