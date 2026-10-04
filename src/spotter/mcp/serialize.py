"""Compact JSON values for tool output. Weights leave as lb, via `spotter.units` only."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from spotter.units import kg_to_lb


def lb(kg: Decimal | None) -> float | None:
    """Display weight. None stays None (bodyweight or unknown)."""
    return None if kg is None else float(kg_to_lb(kg))


def num(value: Decimal | int | None, places: int = 1) -> float | None:
    return None if value is None else round(float(value), places)


def iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return value.isoformat()


def minutes(seconds: int | Decimal | None) -> float | None:
    return None if seconds is None else round(float(seconds) / 60, 1)


def km(meters: Decimal | None) -> float | None:
    return None if meters is None else round(float(meters) / 1000, 2)
