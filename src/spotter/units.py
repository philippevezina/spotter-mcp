"""Weight conversion. The only module that converts between lb and kg (SPEC section 6).

Rule: round to the achievable lb increment first, then convert. Display rounds to 0.5 lb.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

KG_PER_LB = Decimal("0.45359237")  # exact by definition
STORAGE_KG = Decimal("0.001")  # NUMERIC(7,3)
GARMIN_STEP_KG = Decimal("0.01")  # watch shows no weight when steps are sent in grams
DISPLAY_LB = Decimal("0.5")

Number = Decimal | int | float | str


def _dec(value: Number) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _round_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).quantize(Decimal(1), ROUND_HALF_UP) * step


def round_lb(lb: Number, increment_lb: Number) -> Decimal:
    """Round a load to the nearest achievable increment, half up."""
    inc = _dec(increment_lb)
    if inc <= 0:
        raise ValueError("increment_lb must be positive")
    return _round_to(_dec(lb), inc)


def lb_to_kg(lb: Number, increment_lb: Number) -> Decimal:
    """Storage value: round to the increment, convert, keep 3 decimals."""
    return (round_lb(lb, increment_lb) * KG_PER_LB).quantize(STORAGE_KG, ROUND_HALF_UP)


def garmin_step_kg(lb: Number, increment_lb: Number) -> Decimal:
    """Workout step `weightValue`: kg with 2 decimals, from the exact conversion.

    Rounded once from the exact value, never from the 3-decimal storage value.
    """
    return (round_lb(lb, increment_lb) * KG_PER_LB).quantize(GARMIN_STEP_KG, ROUND_HALF_UP)


def kg_to_lb(kg: Number) -> Decimal:
    """Display value: convert and round to 0.5 lb."""
    return _round_to(_dec(kg) / KG_PER_LB, DISPLAY_LB)


def grams_to_kg(grams: Number) -> Decimal:
    """Performed sets report `weight` in grams."""
    return (_dec(grams) / 1000).quantize(STORAGE_KG, ROUND_HALF_UP)
