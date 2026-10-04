"""Mappers against recorded, anonymized Garmin fixtures."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

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


# Daily metrics: fixtures keep Garmin's real shapes with fake numbers (decisions.md).
# The recording account was onboarding: HRV baseline and readiness score are null.

YESTERDAY, TODAY = date(2025, 10, 4), date(2025, 10, 5)


def metrics(name: str) -> Any:
    return load(f"metrics/{name}.json")


def test_index_hrv_by_date() -> None:
    by_day = mappers.index_hrv(metrics("hrv_range"))
    assert sorted(by_day) == [YESTERDAY, TODAY]
    row = mappers.map_daily_metrics(YESTERDAY, {"hrv": by_day[YESTERDAY]})
    assert row["hrv_overnight_ms"] == 44
    assert row["hrv_status"] is None  # "NONE" while onboarding means no status
    assert row["hrv_baseline_low"] is None and row["hrv_baseline_high"] is None


def test_hrv_status_and_baseline_when_present() -> None:
    # Baseline shape from Garmin's HRV endpoint; not yet in a recording (onboarding).
    entry = {
        **mappers.index_hrv(metrics("hrv_range"))[TODAY],
        "status": "unbalanced",
        "baseline": {"lowUpper": 40, "balancedLow": 46, "balancedUpper": 62, "markerValue": 0.4},
    }
    row = mappers.map_daily_metrics(TODAY, {"hrv": entry})
    assert (row["hrv_status"], row["hrv_overnight_ms"]) == ("UNBALANCED", 52)
    assert (row["hrv_baseline_low"], row["hrv_baseline_high"]) == (46, 62)


def test_max_metrics() -> None:
    by_day = mappers.index_max_metrics(metrics("max_metrics_range"))
    assert list(by_day) == [YESTERDAY]
    row = mappers.map_daily_metrics(YESTERDAY, {"max_metrics": by_day[YESTERDAY]})
    assert (row["vo2max_run"], row["vo2max_bike"]) == (Decimal("51.3"), None)
    assert mappers.index_max_metrics({}) == {}


def test_weight_is_grams_to_kg_and_last_of_day_wins() -> None:
    payload = metrics("body_composition_range")
    by_day = mappers.index_weights(payload)
    assert list(by_day) == [date(2025, 10, 3)]
    row = mappers.map_daily_metrics(date(2025, 10, 3), {"weight": by_day[date(2025, 10, 3)]})
    assert row["bodyweight_kg"] == Decimal("80.00")
    first = payload["dateWeightList"][0]
    later = {**first, "date": first["date"] + 3_600_000, "weight": 79550.0}
    twice = mappers.index_weights({"dateWeightList": [later, first]})
    assert twice[date(2025, 10, 3)]["weight"] == 79550.0


def test_sleep() -> None:
    row = mappers.map_daily_metrics(TODAY, {"sleep": metrics("sleep_today")})
    assert (row["sleep_score"], row["sleep_s"]) == (58, 23400)


def test_readiness_picks_the_morning_snapshot() -> None:
    entries = metrics("training_readiness_today")
    assert [e["inputContext"] for e in entries] == [
        "AFTER_POST_EXERCISE_RESET",
        "AFTER_WAKEUP_RESET",
    ]
    row = mappers.map_daily_metrics(TODAY, {"readiness": entries})
    assert row["training_readiness"] is None and row["acute_load"] is None  # onboarding
    scored = [{**entries[0], "score": 30}, {**entries[1], "score": 71, "acuteLoad": 412.4}]
    row = mappers.map_daily_metrics(TODAY, {"readiness": scored})
    assert (row["training_readiness"], row["acute_load"]) == (71, Decimal("412.4"))
    first_only = mappers.map_daily_metrics(TODAY, {"readiness": [{**entries[0], "score": 30}]})
    assert first_only["training_readiness"] == 30


def test_daily_summary() -> None:
    row = mappers.map_daily_metrics(YESTERDAY, {"summary": metrics("daily_summary_yesterday")})
    assert (row["resting_hr"], row["body_battery_max"], row["body_battery_min"]) == (52, 88, 22)
    assert row["stress_avg"] == 31
    today = mappers.map_daily_metrics(TODAY, {"summary": metrics("daily_summary_today")})
    assert today["stress_avg"] is None  # -1: not enough data


def test_daily_row_has_only_fetched_sources() -> None:
    row = mappers.map_daily_metrics(TODAY, {"sleep": metrics("sleep_today"), "summary": None})
    assert set(row) == {
        "local_date",
        "sleep_score",
        "sleep_s",
        "resting_hr",
        "body_battery_max",
        "body_battery_min",
        "stress_avg",
        "raw_json",
    }
    assert row["resting_hr"] is None  # fetched, nothing that day
    assert set(row["raw_json"]) == {"sleep", "summary"}


def test_raw_json_drops_time_series() -> None:
    sleep = {**metrics("sleep_today"), "sleepHeartRate": [{"value": 1}] * 50}
    raw = mappers.map_daily_metrics(TODAY, {"sleep": sleep})["raw_json"]["sleep"]
    assert "sleepHeartRate" not in raw and "dailySleepDTO" in raw
    assert not any(isinstance(v, list) for v in raw.values())


def test_empty_payloads_map_to_nulls() -> None:
    sources = {
        "hrv": None,
        "max_metrics": None,
        "weight": None,
        "sleep": {},
        "readiness": [],
        "summary": {},
    }
    row = mappers.map_daily_metrics(TODAY, sources)
    for columns in mappers.SOURCE_COLUMNS.values():
        assert all(row[c] is None for c in columns)
    assert mappers.index_hrv({}) == {} and mappers.index_weights({}) == {}


def test_unknown_source_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown daily metrics source"):
        mappers.map_daily_metrics(TODAY, {"steps": {}})
