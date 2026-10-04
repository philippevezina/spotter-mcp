"""Daily readiness classification (coaching-rules.md A.5).

Reasons are machine-readable strings, e.g. `training_readiness:18<25`. Missing
inputs add `unknown:<field>` and never make a day red on their own.
"""

from __future__ import annotations

from collections.abc import Iterable

from spotter.engine.rules import (
    BODY_BATTERY_MAX_RED_BELOW,
    HRV_STATUS_RED,
    READINESS_AMBER_BELOW,
    READINESS_RED_BELOW,
    SLEEP_AMBER_BELOW,
    SLEEP_RED_BELOW,
)
from spotter.engine.types import (
    DailyReadinessInput,
    ReadinessClass,
    ReadinessDay,
    ReadinessWindow,
)

FIELDS = (
    "hrv_status",
    "hrv_overnight_ms",
    "hrv_baseline_low",
    "training_readiness",
    "sleep_score",
    "body_battery_max",
)


def _red(d: DailyReadinessInput) -> list[str]:
    reasons = []
    if d.hrv_status is not None and d.hrv_status.upper() in HRV_STATUS_RED:
        reasons.append(f"hrv_status:{d.hrv_status.upper()}")
    if d.training_readiness is not None and d.training_readiness < READINESS_RED_BELOW:
        reasons.append(f"training_readiness:{d.training_readiness}<{READINESS_RED_BELOW}")
    if (
        d.sleep_score is not None
        and d.body_battery_max is not None
        and d.sleep_score < SLEEP_RED_BELOW
        and d.body_battery_max < BODY_BATTERY_MAX_RED_BELOW
    ):
        reasons.append(
            f"sleep_score:{d.sleep_score}<{SLEEP_RED_BELOW}"
            f"+body_battery_max:{d.body_battery_max}<{BODY_BATTERY_MAX_RED_BELOW}"
        )
    return reasons


def _amber(d: DailyReadinessInput) -> list[str]:
    reasons = []
    if (
        d.hrv_overnight_ms is not None
        and d.hrv_baseline_low is not None
        and d.hrv_overnight_ms < d.hrv_baseline_low
    ):
        reasons.append(f"hrv_below_baseline:{d.hrv_overnight_ms}<{d.hrv_baseline_low}")
    if (
        d.training_readiness is not None
        and READINESS_RED_BELOW <= d.training_readiness < READINESS_AMBER_BELOW
    ):
        reasons.append(f"training_readiness:{d.training_readiness}<{READINESS_AMBER_BELOW}")
    if d.sleep_score is not None and d.sleep_score < SLEEP_AMBER_BELOW:
        reasons.append(f"sleep_score:{d.sleep_score}<{SLEEP_AMBER_BELOW}")
    return reasons


def classify(d: DailyReadinessInput) -> ReadinessDay:
    """Red if any red rule fires, else amber if any amber rule fires, else green.

    Reasons list every rule that fired (red first), then every missing input.
    """
    red, amber = _red(d), _amber(d)
    missing = [f for f in FIELDS if getattr(d, f) is None]
    cls: ReadinessClass = "red" if red else "amber" if amber else "green"
    return ReadinessDay(
        local_date=d.local_date,
        readiness_class=cls,
        reasons=(*red, *amber, *(f"unknown:{f}" for f in missing)),
        has_data=len(missing) < len(FIELDS),
    )


def summarize_window(days: Iterable[ReadinessDay]) -> ReadinessWindow:
    """Class counts over a window. Days with no data at all count as `no_data` only."""
    counts = {"green": 0, "amber": 0, "red": 0}
    total = no_data = 0
    for day in days:
        total += 1
        if day.has_data:
            counts[day.readiness_class] += 1
        else:
            no_data += 1
    return ReadinessWindow(days=total, no_data=no_data, **counts)
