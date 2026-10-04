"""Mappers against recorded, anonymized Garmin fixtures."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from spotter.garmin import mappers
from spotter.units import kg_to_lb

FIXTURES = Path(__file__).parent / "fixtures" / "garmin"
TORONTO = ZoneInfo("America/Toronto")


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def test_strength_activity_summary() -> None:
    summary = load("strength_activity_summary.json")
    row = mappers.map_activity(summary, TORONTO)
    assert row["garmin_activity_id"] == 900000001
    assert row["type"] == "strength_training"
    assert row["start_time"] == datetime(2025, 10, 5, 18, 32, 25, tzinfo=UTC)
    assert row["local_date"] == date(2025, 10, 5)
    assert row["duration_s"] == 207
    assert row["avg_hr"] == 68 and row["max_hr"] == 82
    assert row["training_load"] == Decimal("0.0")
    assert row["raw_json"] is summary
    assert mappers.is_strength(summary)
    assert mappers.workout_id(summary) == 800000001


def test_local_date_uses_athlete_tz() -> None:
    summary = {**load("strength_activity_summary.json"), "startTimeGMT": "2026-10-05 02:30:00"}
    assert mappers.map_activity(summary, TORONTO)["local_date"] == date(2026, 10, 4)
    assert mappers.map_activity(summary, ZoneInfo("UTC"))["local_date"] == date(2026, 10, 5)


def test_missing_optional_fields_are_null() -> None:
    summary = {
        "activityId": 1,
        "activityType": {"typeKey": "running"},
        "startTimeGMT": "2026-10-04 12:00:00",
    }
    row = mappers.map_activity(summary, TORONTO)
    assert row["duration_s"] is None and row["distance_m"] is None and row["feel"] is None
    assert not mappers.is_strength(summary)
    assert mappers.workout_id(summary) is None


def test_exercise_sets() -> None:
    rows = mappers.map_exercise_sets(load("exercise_sets.json"))
    assert [r["set_index"] for r in rows] == [0, 2]  # REST set (index 1) dropped
    first, zero_rep = rows
    assert (first["garmin_category"], first["garmin_name"]) == (
        "BENCH_PRESS",
        "BARBELL_BENCH_PRESS",
    )
    assert first["weight_kg"] == Decimal("86.187")
    assert kg_to_lb(first["weight_kg"]) == Decimal("190.0")
    assert first["reps"] == 5
    assert first["duration_s"] == Decimal("49.8")
    assert first["start_time"] == datetime(2025, 10, 5, 18, 32, 25, tzinfo=UTC)
    assert zero_rep["reps"] == 0  # kept; stats ignore it


def test_highest_probability_candidate_wins() -> None:
    payload = {
        "exerciseSets": [
            {
                "messageIndex": 0,
                "setType": "ACTIVE",
                "repetitionCount": 8,
                "weight": None,
                "startTime": None,
                "exercises": [
                    {"category": "ROW", "name": "DUMBBELL_ROW", "probability": 20.0},
                    {"category": "PULL_UP", "name": "PULL_UP", "probability": 75.0},
                ],
            },
            {"messageIndex": 1, "setType": "ACTIVE", "repetitionCount": 5, "exercises": []},
        ]
    }
    bodyweight, unknown = mappers.map_exercise_sets(payload)
    assert (bodyweight["garmin_category"], bodyweight["garmin_name"]) == ("PULL_UP", "PULL_UP")
    assert bodyweight["weight_kg"] is None and bodyweight["start_time"] is None
    assert (unknown["garmin_category"], unknown["garmin_name"]) == (None, None)


def test_empty_payload() -> None:
    assert mappers.map_exercise_sets({}) == []
    assert mappers.map_exercise_sets({"exerciseSets": None}) == []


def test_running_activity_summary() -> None:
    row = mappers.map_activity(load("running_activity_summary.json"), TORONTO)
    assert row["type"] == "running"
    assert row["local_date"] == date(2025, 10, 4)
    assert row["distance_m"] == Decimal("10178.7")
    assert row["duration_s"] == 3815
    assert row["elevation_gain_m"] == Decimal("40.0")
    assert row["avg_hr"] == 176
    assert row["perceived_effort"] is None
