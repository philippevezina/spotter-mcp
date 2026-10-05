"""Rules A.3, A.4, A.6, A.7 effect, A.9, A.11, A.12: progression, stalls, deload, modifiers.

Loads in lb. Expected values are computed by hand from coaching-rules.md and decisions.md.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

import pytest

from spotter.engine.progression import (
    apply_modifiers,
    at_working_weight,
    deload,
    judge,
    next_prescription,
    stall_events,
    stall_streak,
)
from spotter.engine.types import (
    ExerciseProposal,
    Exposure,
    LoadedSet,
    Prescription,
    StallEvent,
)

D = Decimal
START = date(2026, 9, 7)  # plan start, a Monday


def rx(**kw: Any) -> Prescription:
    base: dict[str, Any] = {
        "sets": 3,
        "rep_min": 5,
        "rep_max": 5,
        "target_rir": 2,
        "rest_s": 180,
        "role": "main",
        "model": "linear",
        "increment_lb": D(5),
        "load_type": "barbell",
        "lower_body": False,
    }
    return Prescription(**{**base, **kw})


def ex(day: int, *sets: tuple[float | None, int], **kw: Any) -> Exposure:
    """Exposure `day` days after the plan start. Sets as (lb or None, reps)."""
    return Exposure(
        local_date=START + timedelta(days=day),
        sets=tuple(LoadedSet(None if w is None else D(str(w)), r) for w, r in sets),
        **kw,
    )


def three(w: float | None, *reps: int) -> tuple[tuple[float | None, int], ...]:
    return tuple((w, r) for r in reps)


# Working weight and judging ------------------------------------------------------


def test_working_weight_ignores_warmups_and_backoffs() -> None:
    e = ex(0, (95, 8), (185, 5), (185, 5), (165, 8))
    assert at_working_weight(e) == (D(185), (5, 5))


def test_working_weight_bodyweight_and_empty() -> None:
    assert at_working_weight(ex(0, (None, 8), (None, 6))) == (None, (8, 6))
    assert at_working_weight(ex(0, (100, 0))) == (None, ())


@pytest.mark.parametrize(
    ("prescription", "exposure", "verdict", "reasons"),
    [
        pytest.param(rx(), ex(0, *three(225, 5, 5, 5)), "hit", (), id="linear hit"),
        pytest.param(
            rx(), ex(0, *three(225, 5, 5, 4)), "miss", ("reps_below_min:4<5",), id="linear miss"
        ),
        pytest.param(
            rx(), ex(0, *three(225, 5, 5)), "short", ("sets_short:2<3",), id="short session"
        ),
        pytest.param(
            rx(rep_min=3, rep_max=5),
            ex(0, *three(225, 5, 4, 5)),
            "hold_range",
            ("reps_below_target:4<5",),
            id="linear between min and target",
        ),
        pytest.param(
            rx(model="double_progression", rep_min=8, rep_max=12),
            ex(0, *three(60, 9, 8, 8)),
            "hit",
            (),
            id="double: rep_min reached is a hit",
        ),
        pytest.param(
            rx(),
            ex(0, *three(225, 5, 5, 5, 3)),
            "hit",
            (),
            id="only the first planned sets count",
        ),
        pytest.param(
            rx(model="double_progression", rep_min=8, rep_max=12),
            ex(0, *three(60, 10, 9, 9), targets=(10, 10, 10)),
            "hold_range",
            ("reps_below_target:9<10",),
            id="stored targets (Phase 5)",
        ),
    ],
)
def test_judge(
    prescription: Prescription, exposure: Exposure, verdict: str, reasons: tuple[str, ...]
) -> None:
    assert judge(prescription, exposure) == (verdict, reasons)


# Stalls (A.4) -------------------------------------------------------------------

MISS = three(115, 5, 4, 4)
HIT = three(115, 5, 5, 5)


@pytest.mark.parametrize(
    ("history", "streak"),
    [
        pytest.param([], 0, id="no history"),
        pytest.param([ex(0, *HIT)], 0, id="hit"),
        pytest.param([ex(0, *MISS), ex(7, *MISS)], 2, id="two misses"),
        pytest.param([ex(0, *MISS), ex(7, *HIT), ex(14, *MISS)], 1, id="hit ends the streak"),
        pytest.param(
            [ex(0, *three(110, 5, 4, 4)), ex(7, *MISS)], 1, id="weight change ends the streak"
        ),
        pytest.param(
            [ex(0, *MISS), ex(3, *three(115, 5, 5)), ex(7, *MISS)], 2, id="short is skipped"
        ),
        pytest.param(
            [ex(0, *MISS), ex(3, *three(105, 3, 3, 3), deload=True), ex(7, *MISS)],
            2,
            id="deload is skipped",
        ),
    ],
)
def test_stall_streak(history: list[Exposure], streak: int) -> None:
    assert stall_streak(rx(), history) == streak


def test_stall_events() -> None:
    history = [ex(0, *MISS), ex(7, *MISS), ex(14, *MISS), ex(21, *HIT)]
    assert stall_events(rx(), history) == [
        StallEvent(START + timedelta(days=7), "stall_warning", 2),
        StallEvent(START + timedelta(days=14), "stall", 3),
    ]


# Next prescription: calibration and estimates (A.12, decisions.md) ---------------


def test_no_history_needs_calibration() -> None:
    p = next_prescription(rx(), [], START)
    assert (p.status, p.weight_lb, p.rule_fired, p.reasons) == (
        "needs_calibration",
        None,
        "needs_calibration",
        ("no_history_12w",),
    )
    assert p.target_reps == (5, 5, 5)


def test_only_deload_history_needs_calibration() -> None:
    assert next_prescription(rx(), [ex(0, *HIT, deload=True)], START).status == (
        "needs_calibration"
    )


def test_estimate_from_history_scenario_5() -> None:
    """Deadlift 315 x 5 before the plan; plan 3 x 8-10 at RIR 2.

    315 x (1 + 5/30) = 367.5; 367.5 / (1 + 10/30) = 275.625; floor to 5 -> 275.
    """
    deadlift = rx(model="double_progression", rep_min=8, rep_max=10, lower_body=True)
    p = next_prescription(deadlift, [ex(-5, (135, 5), (315, 5))], START)
    assert (p.status, p.weight_lb, p.target_reps, p.rule_fired) == (
        "ok",
        D(275),
        (8, 8, 8),
        "estimate_from_history",
    )
    assert p.reasons == ("e1rm_lb:367.5", "solved_for_reps:10")


def test_estimate_uses_best_set_and_barbell_floor() -> None:
    curl = rx(model="double_progression", rep_min=12, rep_max=15, target_rir=3)
    history = [ex(-10, *three(50, 5, 5, 5)), ex(-3, *three(40, 6, 6, 6))]
    # best: 50 x (1 + 5/30) = 58.33; / (1 + 15/30) = 38.9 -> 35 -> barbell floor 45
    assert next_prescription(curl, history, START).weight_lb == D(45)


@pytest.mark.parametrize(
    ("history", "prescription", "reason"),
    [
        pytest.param(
            [ex(-3, *three(None, 15, 15, 15))],
            rx(model="double_progression", rep_min=6, rep_max=10, load_type="bodyweight"),
            "bodyweight_new_rep_range",
            id="bodyweight",
        ),
        pytest.param(
            [ex(-3, *three(30, 15, 15, 15))],
            rx(model="double_progression", rep_min=6, rep_max=10),
            "no_estimable_history",
            id="only sets above 10 reps",
        ),
        pytest.param(
            [ex(-3, *three(30, 5, 5, 5))],
            rx(model="double_progression", rep_min=8, rep_max=10, increment_lb=None),
            "no_estimable_history",
            id="no increment",
        ),
    ],
)
def test_estimate_falls_back_to_calibration(
    history: list[Exposure], prescription: Prescription, reason: str
) -> None:
    p = next_prescription(prescription, history, START)
    assert (p.status, p.reasons) == ("needs_calibration", (reason,))


def test_pre_plan_history_in_range_uses_the_model() -> None:
    p = next_prescription(rx(), [ex(-3, *three(225, 5, 5, 5))], START)
    assert (p.weight_lb, p.rule_fired) == (D(230), "linear:+5lb")


# Next prescription: models (A.3) ---------------------------------------------------


def run(prescription: Prescription, *history: Exposure) -> ExerciseProposal:
    return next_prescription(prescription, list(history), START)


SQUAT = rx(lower_body=True)
ROW = rx(
    model="double_progression", role="accessory", rep_min=8, rep_max=12, load_type="dumbbell_each"
)
BENCH = rx(model="double_progression", role="secondary", rep_min=6, rep_max=8)
OHP = rx()


@pytest.mark.parametrize(
    ("prescription", "history", "weight", "targets", "rule"),
    [
        # Exit scenarios 1 to 4 (decisions.md).
        pytest.param(
            SQUAT, [ex(0, *three(225, 5, 5, 5))], 235, (5, 5, 5), "linear:+10lb", id="scenario 1"
        ),
        pytest.param(
            ROW,
            [ex(0, *three(60, 10, 10, 9))],
            60,
            (11, 11, 10),
            "double_progression:+1rep",
            id="scenario 2",
        ),
        pytest.param(
            BENCH,
            [ex(0, *three(185, 8, 8, 8))],
            190,
            (6, 6, 6),
            "double_progression:+5lb",
            id="scenario 3",
        ),
        pytest.param(
            OHP,
            [ex(0, *MISS), ex(7, *MISS), ex(14, *MISS)],
            100,
            (5, 5, 5),
            "reset:-10%",
            id="scenario 4",
        ),
        # linear
        pytest.param(OHP, [ex(0, *HIT)], 120, (5, 5, 5), "linear:+5lb", id="linear upper +5"),
        pytest.param(
            rx(increment_lb=D("2.5"), lower_body=True),
            [ex(0, *three(100, 5, 5, 5))],
            110,
            (5, 5, 5),
            "linear:+10lb",
            id="linear lower, 2.5 increment",
        ),
        pytest.param(
            rx(increment_lb=D(10)),
            [ex(0, *three(100, 5, 5, 5))],
            110,
            (5, 5, 5),
            "linear:+10lb",
            id="linear step at least one increment",
        ),
        pytest.param(OHP, [ex(0, *MISS)], 115, (5, 5, 5), "hold:miss", id="linear miss"),
        pytest.param(
            rx(rep_min=3, rep_max=5),
            [ex(0, *three(115, 5, 4, 4))],
            115,
            (5, 5, 5),
            "hold:below_target",
            id="linear below target, not a miss",
        ),
        pytest.param(
            OHP, [ex(0, *three(115, 5, 5))], 115, (5, 5, 5), "hold:sets_short", id="short"
        ),
        # double progression
        pytest.param(
            ROW,
            [ex(0, *three(60, 12, 11, 7))],
            60,
            (8, 8, 8),
            "hold:miss",
            id="double miss resets targets to rep_min",
        ),
        pytest.param(
            ROW,
            [ex(0, *three(60, 12, 11))],
            60,
            (12, 11, 11),
            "hold:sets_short",
            id="double short keeps targets",
        ),
        pytest.param(
            BENCH,
            [ex(0, *three(185, 8, 8, 8), rir=1)],
            190,
            (6, 6, 6),
            "double_progression:+5lb",
            id="double RIR target-1 still adds load",
        ),
        pytest.param(
            BENCH,
            [ex(0, *three(185, 8, 8, 8), rir=0)],
            185,
            (8, 8, 8),
            "hold:rir_low",
            id="double RIR below target-1 holds",
        ),
        pytest.param(
            BENCH,
            [ex(0, *three(185, 8, 8, 8), rir=4)],
            190,
            (6, 6, 6),
            "double_progression:+5lb",
            id="double easy session adds load",
        ),
        pytest.param(
            rx(model="double_progression", rep_min=6, rep_max=10, load_type="bodyweight"),
            [ex(0, *three(None, 10, 10, 10))],
            None,
            (10, 10, 10),
            "hold:bodyweight_at_rep_max",
            id="bodyweight at rep_max",
        ),
        pytest.param(
            rx(model="double_progression", rep_min=6, rep_max=10, load_type="bodyweight"),
            [ex(0, *three(None, 8, 7, 6))],
            None,
            (9, 8, 7),
            "double_progression:+1rep",
            id="bodyweight adds reps",
        ),
        pytest.param(
            rx(rep_min=6, rep_max=10, load_type="bodyweight"),
            [ex(0, *three(None, 8, 7, 6))],
            None,
            (9, 8, 7),
            "double_progression:+1rep",
            id="linear bodyweight falls back to double",
        ),
        pytest.param(
            rx(
                model="double_progression", rep_min=8, rep_max=12, increment_lb=None, load_type=None
            ),
            [ex(0, *three(60, 12, 12, 12))],
            60,
            (12, 12, 12),
            "hold:bodyweight_at_rep_max",
            id="no increment cannot add load",
        ),
        pytest.param(
            rx(model="double_progression", rep_min=8, rep_max=12),
            [ex(0, *three(62.5, 12, 12, 12))],
            70,
            (8, 8, 8),
            "double_progression:+5lb",
            id="off-grid increase rounds to the increment",
        ),
        pytest.param(
            rx(model="double_progression", rep_min=8, rep_max=12),
            [ex(0, *three(62.5, 9, 9, 9))],
            60,
            (10, 10, 10),
            "double_progression:+1rep",
            id="off-grid hold snaps down",
        ),
    ],
)
def test_models(
    prescription: Prescription,
    history: list[Exposure],
    weight: int | None,
    targets: tuple[int, ...],
    rule: str,
) -> None:
    p = run(prescription, *history)
    assert p.status == "ok"
    assert p.weight_lb == (None if weight is None else D(weight))
    assert (p.target_reps, p.rule_fired) == (targets, rule)


RIR = rx(model="rir_based", role="main", rep_min=6, rep_max=10, target_rir=2)


@pytest.mark.parametrize(
    ("history", "weight", "targets", "rule"),
    [
        pytest.param(
            [ex(0, *three(200, 8, 8, 8), rir=4)], 210, (8, 8, 8), "rir_based:+10lb", id="RIR +2"
        ),
        pytest.param(
            [ex(0, *three(200, 8, 8, 8), rir=3)], 205, (8, 8, 8), "rir_based:+5lb", id="RIR +1"
        ),
        pytest.param(
            [ex(0, *three(200, 8, 8, 8), rir=2)],
            200,
            (9, 9, 9),
            "rir_based:+1rep",
            id="RIR on target",
        ),
        pytest.param(
            [ex(0, *three(200, 10, 10, 10), rir=1)],
            200,
            (10, 10, 10),
            "rir_based:+1rep",
            id="RIR target-1 is within target, capped at rep_max",
        ),
        pytest.param(
            [ex(0, *three(200, 8, 8, 8), rir=0)],
            200,
            (8, 8, 8),
            "hold:rir_low",
            id="RIR low once",
        ),
        pytest.param(
            [ex(0, *three(200, 8, 8, 8), rir=0), ex(7, *three(200, 8, 8, 8), rir=0)],
            195,
            (8, 8, 8),
            "rir_based:-5lb",
            id="RIR low twice",
        ),
        pytest.param(
            [ex(0, *three(205, 8, 8, 8), rir=0), ex(7, *three(200, 8, 8, 8), rir=0)],
            200,
            (8, 8, 8),
            "hold:rir_low",
            id="RIR low twice at different weights",
        ),
        pytest.param(
            [ex(0, *three(200, 8, 8, 8), rir=3), ex(7, *three(200, 8, 8, 8), rir=0)],
            200,
            (8, 8, 8),
            "hold:rir_low",
            id="previous RIR fine",
        ),
        pytest.param(
            [ex(-3, *three(200, 8, 8, 8), rir=0), ex(7, *three(200, 8, 8, 8), rir=0)],
            200,
            (8, 8, 8),
            "hold:rir_low",
            id="previous exposure before the plan does not count",
        ),
        pytest.param(
            [ex(0, *three(200, 10, 10, 10))],
            205,
            (6, 6, 6),
            "double_progression:+5lb",
            id="RIR missing falls back to double",
        ),
    ],
)
def test_rir_based(
    history: list[Exposure], weight: int, targets: tuple[int, ...], rule: str
) -> None:
    p = run(RIR, *history)
    assert (p.weight_lb, p.target_reps, p.rule_fired) == (D(weight), targets, rule)


def test_rir_missing_reason() -> None:
    assert run(RIR, ex(0, *three(200, 10, 10, 10))).reasons == ("rir_missing:double_progression",)


# Stalls in proposals (A.4) ------------------------------------------------------------


def test_two_misses_hold_with_stall_warning() -> None:
    p = run(OHP, ex(0, *MISS), ex(7, *MISS))
    assert (p.weight_lb, p.rule_fired, p.flags) == (D(115), "hold:miss", ("stall_warning",))
    assert p.reasons == ("reps_below_min:4<5", "misses:2")


def test_reset_scenario_4_detail() -> None:
    p = run(OHP, ex(0, *MISS), ex(7, *MISS), ex(14, *MISS))
    assert (p.flags, p.reasons) == (("stall",), ("reps_below_min:4<5", "misses:3"))
    assert (p.last_weight_lb, p.last_reps) == (D(115), (5, 4, 4))


def test_reset_rounds_down_and_respects_barbell_floor() -> None:
    p = run(rx(), *(ex(d, *three(50, 5, 4, 4)) for d in (0, 7, 14)))
    assert p.weight_lb == D(45)  # 45 floor; 50 x 0.9 = 45 -> 45


def test_bodyweight_stall_holds_with_flag() -> None:
    pullup = rx(model="double_progression", rep_min=6, rep_max=10, load_type="bodyweight")
    p = run(pullup, *(ex(d, *three(None, 6, 5, 5)) for d in (0, 7, 14)))
    assert (p.weight_lb, p.rule_fired, p.flags, p.target_reps) == (
        None,
        "hold:miss",
        ("stall",),
        (6, 6, 6),
    )


def test_misses_before_the_plan_do_not_stall() -> None:
    p = run(OHP, ex(-14, *MISS), ex(-7, *MISS), ex(0, *MISS))
    assert (p.rule_fired, p.flags) == ("hold:miss", ())


# Deload (A.9) ---------------------------------------------------------------------------


def test_deload_halves_sets_and_drops_load() -> None:
    base = run(SQUAT, ex(0, *three(225, 5, 5, 5)))
    p = deload(SQUAT, base)
    # sets ceil(3 / 2) = 2; 225 x 0.9 = 202.5 -> nearest 5, half up -> 205; RIR 2 + 2
    assert (p.sets, p.weight_lb, p.target_reps, p.target_rir, p.rule_fired) == (
        2,
        D(205),
        (5, 5),
        4,
        "deload",
    )
    assert p.modifiers == ("deload",)


def test_deload_bodyweight_and_calibration_pass_through() -> None:
    pullup = rx(model="double_progression", rep_min=6, rep_max=10, load_type="bodyweight")
    p = deload(pullup, run(pullup, ex(0, *three(None, 8, 7, 6))))
    assert (p.sets, p.weight_lb, p.target_reps) == (2, None, (8, 7))
    cal = run(OHP)
    assert deload(OHP, cal) is cal


def test_deload_uses_barbell_floor() -> None:
    p = deload(OHP, run(OHP, ex(0, *three(45, 5, 5, 5))))
    assert p.weight_lb == D(45)


# Readiness and endurance modifiers (A.6, A.7) ----------------------------------------------


def test_green_or_unknown_changes_nothing() -> None:
    base = run(SQUAT, ex(0, *three(225, 5, 5, 5)))
    assert apply_modifiers(SQUAT, base, "green", False) == base
    assert apply_modifiers(SQUAT, base, None, False) == base


def test_amber_keeps_last_weight_and_reps() -> None:
    base = run(BENCH, ex(0, *three(185, 8, 8, 8)))
    p = apply_modifiers(BENCH, base, "amber", False)
    assert (p.weight_lb, p.target_reps, p.sets, p.target_rir, p.modifiers) == (
        D(185),
        (8, 8, 8),
        3,
        2,
        ("readiness:amber",),
    )


def test_amber_blocks_rep_progression_too() -> None:
    base = run(ROW, ex(0, *three(60, 10, 10, 9)))
    p = apply_modifiers(ROW, base, "amber", False)
    assert (p.weight_lb, p.target_reps) == (D(60), (10, 10, 9))


def test_amber_lets_a_reset_through() -> None:
    base = run(OHP, ex(0, *MISS), ex(7, *MISS), ex(14, *MISS))
    assert apply_modifiers(OHP, base, "amber", False) == base


def test_amber_with_no_progression_changes_nothing() -> None:
    base = run(OHP, ex(0, *MISS))
    assert apply_modifiers(OHP, base, "amber", False) == base


def test_red_main_lift() -> None:
    base = run(SQUAT, ex(0, *three(225, 5, 5, 5)))
    p = apply_modifiers(SQUAT, base, "red", False)
    assert (p.weight_lb, p.sets, p.target_reps, p.target_rir, p.modifiers) == (
        D(225),
        2,
        (5, 5),
        3,
        ("readiness:red",),
    )


@pytest.mark.parametrize(
    ("role", "sets", "expected"),
    [("secondary", 3, 2), ("main", 2, 2), ("main", 1, 1), ("accessory", 3, 3)],
)
def test_red_set_removal(role: str, sets: int, expected: int) -> None:
    prescription = rx(role=role, sets=sets)
    base = run(prescription, ex(0, *three(115, *(4,) * sets)))  # a miss: no progression
    p = apply_modifiers(prescription, base, "red", False)
    assert (p.sets, len(p.target_reps), p.target_rir) == (expected, expected, 3)


def test_interference_blocks_lower_body_main_load_increase() -> None:
    base = run(SQUAT, ex(0, *three(225, 5, 5, 5)))
    p = apply_modifiers(SQUAT, base, None, True)
    assert (p.weight_lb, p.target_reps, p.modifiers) == (
        D(225),
        (5, 5, 5),
        ("endurance_interference",),
    )


@pytest.mark.parametrize(
    "prescription",
    [
        pytest.param(OHP, id="upper body unaffected"),
        pytest.param(replace(SQUAT, role="secondary"), id="secondary unaffected"),
    ],
)
def test_interference_leaves_other_lifts(prescription: Prescription) -> None:
    base = run(prescription, ex(0, *three(225, 5, 5, 5)))
    assert apply_modifiers(prescription, base, None, True) == base


def test_interference_without_a_load_increase() -> None:
    base = run(SQUAT, ex(0, *three(225, 5, 5, 4)))
    assert apply_modifiers(SQUAT, base, None, True) == base
    pistol = rx(
        model="double_progression", rep_min=6, rep_max=10, load_type="bodyweight", lower_body=True
    )
    bw = run(pistol, ex(0, *three(None, 6, 6, 6)))
    assert apply_modifiers(pistol, bw, None, True) == bw


def test_modifiers_pass_calibration_through() -> None:
    cal = run(OHP)
    assert apply_modifiers(OHP, cal, "red", True) is cal
