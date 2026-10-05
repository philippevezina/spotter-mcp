"""Rule A.8: volume guardrails checked in create_plan."""

from __future__ import annotations

from decimal import Decimal

from spotter.engine.types import VolumeDay, VolumeExercise
from spotter.engine.volume import check, weekly_sets_per_muscle

SQUAT = VolumeExercise(4, ("quads", "glutes"), ("hamstrings",))
RDL = VolumeExercise(3, ("hamstrings", "glutes"))
BENCH = VolumeExercise(4, ("chest",), ("triceps", "shoulders"))
ROW = VolumeExercise(4, ("back",), ("biceps",))

LOWER = VolumeDay("Lower A", (SQUAT, RDL))
UPPER = VolumeDay("Upper A", (BENCH, ROW))


def test_weekly_sets_count_secondary_as_half() -> None:
    assert weekly_sets_per_muscle([LOWER, UPPER, LOWER]) == {
        "back": Decimal(4),
        "biceps": Decimal(2),
        "chest": Decimal(4),
        "glutes": Decimal(14),
        "hamstrings": Decimal(10),
        "quads": Decimal(8),
        "shoulders": Decimal(2),
        "triceps": Decimal(2),
    }


def test_check_low_high_and_new_plan() -> None:
    days = [LOWER, UPPER, LOWER, VolumeDay("Glutes", (VolumeExercise(8, ("glutes",)),))]
    assert check(days, recent_history=True) == [
        "weekly_sets_low:back:4<8",
        "weekly_sets_low:chest:4<8",
        "weekly_sets_high:glutes:22>20",
    ]
    assert check([LOWER, LOWER], recent_history=False) == [
        "new_plan_sets:glutes:14 outside 10-12",
        "new_plan_sets:quads:8 outside 10-12",
    ]


def test_check_new_plan_fractional_sets() -> None:
    day = VolumeDay(
        "Full", (VolumeExercise(11, ("quads",)), VolumeExercise(5, ("chest",), ("quads",)))
    )
    assert check([day], recent_history=False) == [
        "weekly_sets_low:chest:5<8",
        "new_plan_sets:chest:5 outside 10-12",
        "new_plan_sets:quads:13.5 outside 10-12",
    ]


def test_check_session_too_long() -> None:
    day = VolumeDay("Marathon", tuple(VolumeExercise(6, ("chest",)) for _ in range(5)))
    assert check([day], recent_history=True) == [
        "weekly_sets_high:chest:30>20",
        "session_sets_high:Marathon:30>25",
    ]


def test_check_clean_plan() -> None:
    day = VolumeDay("Full", (VolumeExercise(10, ("quads",)), VolumeExercise(12, ("back",))))
    assert check([day], recent_history=False) == []
