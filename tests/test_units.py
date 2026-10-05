from __future__ import annotations

from decimal import Decimal

import pytest

from spotter.units import floor_lb, garmin_step_kg, grams_to_kg, kg_to_lb, lb_to_kg, round_lb


@pytest.mark.parametrize(
    ("lb", "increment", "expected"),
    [
        (187, 5, "185"),
        (187.5, 5, "190"),  # half up
        (182.4, 5, "180"),
        (46, 2.5, "45.0"),
        (47.5, 2.5, "47.5"),
        (101, 10, "100"),
    ],
)
def test_round_lb(lb: float, increment: float, expected: str) -> None:
    assert round_lb(lb, increment) == Decimal(expected)


@pytest.mark.parametrize(
    ("lb", "increment", "expected"),
    [(103.5, 5, "100"), (100, 5, "100"), (104.99, 2.5, "102.5"), (275.625, 5, "275")],
)
def test_floor_lb(lb: float, increment: float, expected: str) -> None:
    assert floor_lb(lb, increment) == Decimal(expected)


def test_floor_lb_rejects_bad_increment() -> None:
    with pytest.raises(ValueError):
        floor_lb(100, -5)


def test_round_lb_rejects_bad_increment() -> None:
    with pytest.raises(ValueError):
        round_lb(100, 0)


# Phase 0 check 3: values the watch displayed correctly.
@pytest.mark.parametrize(
    ("lb", "expected_kg"),
    [(185, "83.91"), (190, "86.18"), (225, "102.06"), (205, "92.99")],
)
def test_garmin_step_kg(lb: int, expected_kg: str) -> None:
    assert garmin_step_kg(lb, 5) == Decimal(expected_kg)


def test_garmin_step_kg_rounds_once_from_exact_value() -> None:
    # 135 lb is 61.23497 kg. Rounding via the 3-decimal storage value would give 61.24.
    assert lb_to_kg(135, 5) == Decimal("61.235")
    assert garmin_step_kg(135, 5) == Decimal("61.23")
    for lb in range(5, 1000, 5):
        exact = Decimal(lb) * Decimal("0.45359237")
        assert garmin_step_kg(lb, 5) == exact.quantize(Decimal("0.01"), rounding="ROUND_HALF_UP")


def test_lb_to_kg_storage_precision() -> None:
    assert lb_to_kg(190, 5) == Decimal("86.183")
    assert lb_to_kg(187, 5) == Decimal("83.915")  # rounded to 185 first


# Phase 0 check 2: bench logged at 190 lb came back as 86187 g.
def test_performed_set_grams_round_trip() -> None:
    kg = grams_to_kg(86187)
    assert kg == Decimal("86.187")
    assert kg_to_lb(kg) == Decimal("190.0")


@pytest.mark.parametrize(("kg", "lb"), [("20", "44.0"), ("20.41", "45.0"), ("0", "0")])
def test_kg_to_lb_display(kg: str, lb: str) -> None:
    assert kg_to_lb(kg) == Decimal(lb)


@pytest.mark.parametrize("increment", [2.5, 5, 10])
def test_round_trip_lb_kg_lb(increment: float) -> None:
    step = Decimal(str(increment))
    for k in range(1, int(500 / step) + 1):
        lb = step * k
        assert kg_to_lb(lb_to_kg(lb, increment)) == lb
        assert kg_to_lb(garmin_step_kg(lb, increment)) == lb
