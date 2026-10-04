"""Garmin JSON -> row dicts for `activities` and `performed_sets`. Pure, no I/O.

Raw Garmin JSON stops here: only `activities.raw_json` keeps the summary, and
it is never returned by tools or logged.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
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


# Daily metrics ---------------------------------------------------------------
# Sources: three range calls indexed by date, three per-day calls. Each source
# owns a fixed set of columns. A source that was fetched but had nothing maps to
# nulls; a source that was not fetched is left out, so an upsert keeps what is
# stored (older days get range data only).

SOURCE_COLUMNS: dict[str, tuple[str, ...]] = {
    "hrv": ("hrv_status", "hrv_overnight_ms", "hrv_baseline_low", "hrv_baseline_high"),
    "max_metrics": ("vo2max_run", "vo2max_bike"),
    "weight": ("bodyweight_kg",),
    "sleep": ("sleep_score", "sleep_s"),
    "readiness": ("training_readiness", "acute_load"),
    "summary": ("resting_hr", "body_battery_max", "body_battery_min", "stress_avg"),
}
MORNING_READINESS = "AFTER_WAKEUP_RESET"
NO_HRV_STATUS = "NONE"  # Garmin's value while the baseline is still being built


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _day(value: Any) -> date | None:
    return date.fromisoformat(value[:10]) if isinstance(value, str) else None


def index_hrv(payload: dict[str, Any]) -> dict[date, dict[str, Any]]:
    """`get_hrv_data_range` -> one summary per calendar date."""
    out = {}
    for entry in payload.get("hrvSummaries") or []:
        day = _day(entry.get("calendarDate"))
        if day is not None:
            out[day] = entry
    return out


def index_max_metrics(payload: Any) -> dict[date, dict[str, Any]]:
    """`get_max_metrics_range` -> one entry per date. `generic` is running, `cycling` is bike."""
    out = {}
    for entry in payload if isinstance(payload, list) else []:
        day = _day(_get(entry, "generic", "calendarDate") or _get(entry, "cycling", "calendarDate"))
        if day is not None:
            out[day] = entry
    return out


def index_weights(payload: dict[str, Any]) -> dict[date, dict[str, Any]]:
    """`get_body_composition` -> the last weigh-in of each date."""
    out: dict[date, dict[str, Any]] = {}
    entries = sorted(payload.get("dateWeightList") or [], key=lambda e: e.get("date") or 0)
    for entry in entries:
        day = _day(entry.get("calendarDate"))
        if day is not None:
            out[day] = entry
    return out


def _morning(entries: Any) -> dict[str, Any] | None:
    """The readiness snapshot taken on waking; else the first, as garminconnect does."""
    if not isinstance(entries, list) or not entries:
        return None
    first: dict[str, Any] = entries[0]
    return next((e for e in entries if e.get("inputContext") == MORNING_READINESS), first)


def _map_source(source: str, payload: Any) -> dict[str, Any]:
    if source == "hrv":
        status = _get(payload, "status")
        return {
            "hrv_status": None if status in (None, NO_HRV_STATUS) else str(status).upper(),
            "hrv_overnight_ms": _int(_get(payload, "lastNightAvg")),
            # Baseline shape not yet seen in fixtures: the account was onboarding (decisions.md).
            "hrv_baseline_low": _int(_get(payload, "baseline", "balancedLow")),
            "hrv_baseline_high": _int(_get(payload, "baseline", "balancedUpper")),
        }
    if source == "max_metrics":
        return {
            "vo2max_run": _dec(_get(payload, "generic", "vo2MaxPreciseValue"), "0.1"),
            "vo2max_bike": _dec(_get(payload, "cycling", "vo2MaxPreciseValue"), "0.1"),
        }
    if source == "weight":
        grams = _get(payload, "weight")
        kg = None if grams is None else grams_to_kg(grams).quantize(Decimal("0.01"))
        return {"bodyweight_kg": kg}
    if source == "sleep":
        dto = _get(payload, "dailySleepDTO")
        return {
            "sleep_score": _int(_get(dto, "sleepScores", "overall", "value")),
            "sleep_s": _int(_get(dto, "sleepTimeSeconds")),
        }
    if source == "readiness":
        morning = _morning(payload)
        return {
            "training_readiness": _int(_get(morning, "score")),
            "acute_load": _dec(_get(morning, "acuteLoad"), "0.1"),
        }
    if source == "summary":
        stress = _int(_get(payload, "averageStressLevel"))
        return {
            "resting_hr": _int(_get(payload, "restingHeartRate")),
            "body_battery_max": _int(_get(payload, "bodyBatteryHighestValue")),
            "body_battery_min": _int(_get(payload, "bodyBatteryLowestValue")),
            # Negative values mean "not enough data" on Garmin's stress scale.
            "stress_avg": None if stress is None or stress < 0 else stress,
        }
    raise ValueError(f"unknown daily metrics source: {source}")


def _trim(payload: Any) -> Any:
    """What raw_json keeps of a source: drop top-level lists (time series)."""
    if isinstance(payload, dict):
        return {k: v for k, v in payload.items() if not isinstance(v, list)}
    return payload


def map_daily_metrics(day: date, sources: dict[str, Any]) -> dict[str, Any]:
    """One `daily_metrics` row from the sources fetched for `day`.

    `sources` maps a SOURCE_COLUMNS key to its payload, or None when the source
    was fetched and had nothing for that day. `raw_json` holds one trimmed copy
    per source; the sync merges it into what is stored.
    """
    row: dict[str, Any] = {"local_date": day}
    for source, payload in sources.items():
        row.update(_map_source(source, payload))
    row["raw_json"] = {source: _trim(payload) for source, payload in sources.items()}
    return row
