"""Progression, stalls, deload and readiness modifiers (coaching-rules.md A.3, A.4, A.6,
A.7 effect, A.9, A.11, A.12). Loads in lb.

Each exposure is judged at its working weight: the top working-set weight (A.1).
Back-off sets below it are ignored. Bodyweight work reads every working set.
Stalls are re-derived from history on every call; nothing is stored.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal

from spotter.engine.e1rm import epley
from spotter.engine.rules import (
    BARBELL_MIN_LB,
    DELOAD_FRACTION,
    DELOAD_RIR_ADD,
    DOUBLE_RIR_TOLERANCE,
    E1RM_MAX_REPS,
    E1RM_MIN_REPS,
    EPLEY_REP_DIVISOR,
    LINEAR_STEP_LOWER_LB,
    LINEAR_STEP_UPPER_LB,
    RED_MIN_SETS,
    RED_RIR_ADD,
    RED_SETS_REMOVE,
    RESET_FRACTION,
    RIR_BIG_JUMP_ABOVE,
    RIR_BIG_JUMP_INCREMENTS,
    RIR_LOW_BELOW,
    STALL_RESET_MISSES,
    STALL_WARNING_MISSES,
)
from spotter.engine.sets import working_sets
from spotter.engine.types import (
    ExerciseProposal,
    Exposure,
    Judgement,
    LoadedSet,
    Prescription,
    ReadinessClass,
    SetPerformance,
    StallEvent,
    StallFlag,
)
from spotter.units import floor_lb, round_lb

# Exposures -------------------------------------------------------------------


def _working(e: Exposure) -> list[LoadedSet]:
    # working_sets compares loads by ratio only, so lb values pass through unchanged.
    perf = [SetPerformance(s.weight_lb, s.reps) for s in e.sets]
    return [LoadedSet(p.weight_kg, p.reps or 0) for p in working_sets(perf)]


def at_working_weight(e: Exposure) -> tuple[Decimal | None, tuple[int, ...]]:
    """Working weight (None for bodyweight) and the reps of the sets done at it, in order."""
    work = _working(e)
    top = max((s.weight_lb or Decimal(0) for s in work), default=Decimal(0))
    if top == 0:
        return None, tuple(s.reps for s in work)
    return top, tuple(s.reps for s in work if s.weight_lb == top)


def _fmt(lb: Decimal) -> str:
    """5, 2.5, 10: no exponent, no trailing zeros."""
    return f"{lb.normalize():f}"


def _target(rx: Prescription) -> int:
    """Reps a set must reach to count toward a hit when no per-set targets are stored."""
    return rx.rep_max if rx.model == "linear" else rx.rep_min


def judge(rx: Prescription, e: Exposure) -> tuple[Judgement, tuple[str, ...]]:
    """Hit, miss, hold_range (between rep_min and target) or short (fewer sets than planned).

    Only the first `rx.sets` sets at the working weight count. A short session is not a
    miss (decisions.md).
    """
    _, reps = at_working_weight(e)
    if len(reps) < rx.sets:
        return "short", (f"sets_short:{len(reps)}<{rx.sets}",)
    done = reps[: rx.sets]
    if min(done) < rx.rep_min:
        return "miss", (f"reps_below_min:{min(done)}<{rx.rep_min}",)
    targets = e.targets or (_target(rx),) * rx.sets
    short_of = [(r, t) for r, t in zip(done, targets, strict=False) if r < t]
    if short_of:
        r, t = short_of[0]
        return "hold_range", (f"reps_below_target:{r}<{t}",)
    return "hit", ()


# Stalls ----------------------------------------------------------------------


def _streaks(rx: Prescription, history: Sequence[Exposure]) -> list[tuple[Exposure, int]]:
    """Miss streak after each counted exposure, oldest first. Deload and short exposures are
    skipped; a hit, a hold or a weight change ends the streak."""
    out: list[tuple[Exposure, int]] = []
    streak, weight = 0, None
    for e in history:
        if e.deload:
            continue
        verdict, _ = judge(rx, e)
        if verdict == "short":
            continue
        w, _ = at_working_weight(e)
        if verdict != "miss":
            streak, weight = 0, None
        else:
            streak = streak + 1 if streak and w == weight else 1
            weight = w
        out.append((e, streak))
    return out


def stall_streak(rx: Prescription, history: Sequence[Exposure]) -> int:
    """Consecutive misses at the same working weight, ending at the newest exposure."""
    streaks = _streaks(rx, history)
    return streaks[-1][1] if streaks else 0


def _flag(misses: int) -> StallFlag | None:
    if misses >= STALL_RESET_MISSES:
        return "stall"
    if misses >= STALL_WARNING_MISSES:
        return "stall_warning"
    return None


def stall_events(rx: Prescription, history: Sequence[Exposure]) -> list[StallEvent]:
    """Every exposure that raised `stall_warning` or `stall`, oldest first."""
    events = []
    for e, misses in _streaks(rx, history):
        flag = _flag(misses)
        if flag is not None:
            events.append(StallEvent(e.local_date, flag, misses))
    return events


# Next prescription -------------------------------------------------------------


def _floor_bar(rx: Prescription, weight: Decimal) -> Decimal:
    if rx.load_type == "barbell" and weight < BARBELL_MIN_LB:
        return BARBELL_MIN_LB
    return weight


def _carry(rx: Prescription, reps: tuple[int, ...]) -> tuple[int, ...]:
    """Same targets: performed reps clamped to the range, padded to `rx.sets`."""
    if rx.model == "linear":
        return (rx.rep_max,) * rx.sets
    clamped = [min(max(r, rx.rep_min), rx.rep_max) for r in reps[: rx.sets]]
    pad = min(clamped, default=rx.rep_min)
    return tuple(clamped + [pad] * (rx.sets - len(clamped)))


def _calibrate(rx: Prescription, reason: str) -> ExerciseProposal:
    return ExerciseProposal(
        status="needs_calibration",
        sets=rx.sets,
        target_reps=(rx.rep_min,) * rx.sets,
        weight_lb=None,
        target_rir=rx.target_rir,
        rest_s=rx.rest_s,
        rule_fired="needs_calibration",
        reasons=(reason,),
    )


def _estimate(rx: Prescription, history: Sequence[Exposure]) -> ExerciseProposal:
    """Load for a new rep range from the best Epley estimate in history (decisions.md)."""
    best = max(
        (
            epley(s.weight_lb, s.reps)
            for e in history
            for s in _working(e)
            if s.weight_lb and E1RM_MIN_REPS <= s.reps <= E1RM_MAX_REPS
        ),
        default=None,
    )
    if best is None or rx.increment_lb is None:
        return _calibrate(rx, "no_estimable_history")
    reps_to_failure = rx.rep_min + rx.target_rir
    weight = best / (1 + Decimal(reps_to_failure) / EPLEY_REP_DIVISOR)
    return ExerciseProposal(
        status="ok",
        sets=rx.sets,
        target_reps=(rx.rep_min,) * rx.sets,
        weight_lb=_floor_bar(rx, floor_lb(weight, rx.increment_lb)),
        target_rir=rx.target_rir,
        rest_s=rx.rest_s,
        rule_fired="estimate_from_history",
        reasons=(f"e1rm_lb:{best.quantize(Decimal('0.1'))}", f"solved_for_reps:{reps_to_failure}"),
    )


def _rir_low(rx: Prescription, rir: int | None) -> bool:
    return rir is not None and rir < rx.target_rir - RIR_LOW_BELOW


Step = tuple[Decimal | None, tuple[int, ...], str, tuple[str, ...]]


def _double(
    rx: Prescription, weight: Decimal | None, reps: tuple[int, ...], rir: int | None
) -> Step:
    done = reps[: rx.sets]
    if all(r >= rx.rep_max for r in done):
        if rir is not None and rir < rx.target_rir - DOUBLE_RIR_TOLERANCE:
            return (
                weight,
                (rx.rep_max,) * rx.sets,
                "hold:rir_low",
                (f"rir:{rir}<{rx.target_rir - DOUBLE_RIR_TOLERANCE}",),
            )
        if weight is None or rx.increment_lb is None:
            return (
                weight,
                (rx.rep_max,) * rx.sets,
                "hold:bodyweight_at_rep_max",
                ("bodyweight_at_rep_max",),
            )
        return (
            weight + rx.increment_lb,
            (rx.rep_min,) * rx.sets,
            f"double_progression:+{_fmt(rx.increment_lb)}lb",
            (),
        )
    return weight, tuple(min(r + 1, rx.rep_max) for r in done), "double_progression:+1rep", ()


def _linear(rx: Prescription, weight: Decimal, verdict: Judgement) -> Step:
    targets = (rx.rep_max,) * rx.sets
    if verdict != "hit":
        return weight, targets, "hold:below_target", ()
    assert rx.increment_lb is not None
    step = LINEAR_STEP_LOWER_LB if rx.lower_body else LINEAR_STEP_UPPER_LB
    step = max(round_lb(step, rx.increment_lb), rx.increment_lb)
    return weight + step, targets, f"linear:+{_fmt(step)}lb", ()


def _rir_based(
    rx: Prescription,
    weight: Decimal,
    reps: tuple[int, ...],
    rir: int,
    previous: Exposure | None,
) -> Step:
    assert rx.increment_lb is not None
    inc = rx.increment_lb
    carry = _carry(rx, reps)
    if rir >= rx.target_rir + RIR_BIG_JUMP_ABOVE:
        jump = inc * RIR_BIG_JUMP_INCREMENTS
        return weight + jump, carry, f"rir_based:+{_fmt(jump)}lb", (f"rir:{rir}",)
    if rir == rx.target_rir + 1:
        return weight + inc, carry, f"rir_based:+{_fmt(inc)}lb", (f"rir:{rir}",)
    if not _rir_low(rx, rir):
        done = reps[: rx.sets]
        return (
            weight,
            tuple(min(r + 1, rx.rep_max) for r in done),
            "rir_based:+1rep",
            (f"rir:{rir}",),
        )
    if (
        previous is not None
        and _rir_low(rx, previous.rir)
        and at_working_weight(previous)[0] == weight
    ):
        return (
            weight - inc,
            carry,
            f"rir_based:-{_fmt(inc)}lb",
            (f"rir:{rir}", "rir_low_twice"),
        )
    return weight, carry, "hold:rir_low", (f"rir:{rir}",)


def next_prescription(
    rx: Prescription, history: Sequence[Exposure], plan_start: date
) -> ExerciseProposal:
    """Next prescription from history, oldest first.

    The caller passes exposures from the last 12 weeks (A.12), deload ones flagged.
    Exposures before `plan_start` set the baseline but never count toward stalls.
    """
    usable = [e for e in history if not e.deload and at_working_weight(e)[1]]
    if not usable:
        return _calibrate(rx, "no_history_12w")
    in_plan = [e for e in usable if e.local_date >= plan_start]
    last = usable[-1]
    weight, reps = at_working_weight(last)

    if not in_plan and not all(rx.rep_min <= r <= rx.rep_max for r in reps):
        if weight is None:
            return _calibrate(rx, "bodyweight_new_rep_range")
        return _estimate(rx, usable)

    verdict, judged = judge(rx, last)
    streak = stall_streak(rx, in_plan)
    flag = _flag(streak) if verdict == "miss" else None
    reasons: tuple[str, ...] = judged
    if flag == "stall" and weight is not None and rx.increment_lb is not None:
        new_weight: Decimal | None = floor_lb(weight * (1 - RESET_FRACTION), rx.increment_lb)
        targets, rule = (rx.rep_min,) * rx.sets, f"reset:-{RESET_FRACTION * 100:.0f}%"
        reasons += (f"misses:{streak}",)
    elif verdict in ("miss", "short"):
        new_weight, targets = weight, _carry(rx, reps)
        if verdict == "miss":
            targets = (rx.rep_min,) * rx.sets if rx.model != "linear" else targets
        rule = f"hold:{'sets_short' if verdict == 'short' else 'miss'}"
        if flag is not None:
            reasons += (f"misses:{streak}",)
    else:
        model = rx.model
        if weight is None or rx.increment_lb is None:
            model = "double_progression"
        elif model == "rir_based" and last.rir is None:
            model = "double_progression"
            reasons += ("rir_missing:double_progression",)
        if model == "linear":
            assert weight is not None
            new_weight, targets, rule, extra = _linear(rx, weight, verdict)
        elif model == "rir_based":
            assert weight is not None and last.rir is not None
            previous = in_plan[-2] if len(in_plan) >= 2 and in_plan[-1] is last else None
            new_weight, targets, rule, extra = _rir_based(rx, weight, reps, last.rir, previous)
        else:
            new_weight, targets, rule, extra = _double(rx, weight, reps, last.rir)
        reasons += extra

    if new_weight is not None and rx.increment_lb is not None:
        snap = round_lb if weight is not None and new_weight > weight else floor_lb
        new_weight = _floor_bar(rx, snap(new_weight, rx.increment_lb))
    return ExerciseProposal(
        status="ok",
        sets=rx.sets,
        target_reps=targets,
        weight_lb=new_weight,
        target_rir=rx.target_rir,
        rest_s=rx.rest_s,
        rule_fired=rule,
        reasons=reasons,
        flags=() if flag is None else (flag,),
        last_weight_lb=weight,
        last_reps=reps,
    )


# Deload and modifiers ------------------------------------------------------------


def deload(rx: Prescription, p: ExerciseProposal) -> ExerciseProposal:
    """A.9: half the sets (rounded up), last weight -10 %, target RIR +2, no progression."""
    if p.status != "ok":
        return p
    sets = -(-rx.sets // 2)
    weight = p.last_weight_lb
    if weight is not None and rx.increment_lb is not None:
        weight = _floor_bar(rx, round_lb(weight * (1 - DELOAD_FRACTION), rx.increment_lb))
    return replace(
        p,
        sets=sets,
        target_reps=_carry(rx, p.last_reps)[:sets],
        weight_lb=weight,
        target_rir=rx.target_rir + DELOAD_RIR_ADD,
        rule_fired="deload",
        flags=(),
        modifiers=(*p.modifiers, "deload"),
    )


def _keep_last(rx: Prescription, p: ExerciseProposal) -> ExerciseProposal:
    return replace(p, weight_lb=p.last_weight_lb, target_reps=_carry(rx, p.last_reps))


def _progresses(rx: Prescription, p: ExerciseProposal) -> bool:
    """More load than last time, or at the same load more reps than holding would ask."""
    last, now = p.last_weight_lb, p.weight_lb
    if last is not None and now is not None and now != last:
        return now > last
    return sum(p.target_reps) > sum(_carry(rx, p.last_reps))


def apply_modifiers(
    rx: Prescription,
    p: ExerciseProposal,
    readiness: ReadinessClass | None,
    interference: bool,
) -> ExerciseProposal:
    """A.6 and the A.7 effect. `readiness` is None when the session is not today.

    amber and red keep the last weight and reps instead of progressing; red also adds 1 to
    target RIR and removes a set from main and secondary lifts. Interference stops load
    increases on lower-body main lifts. Resets and holds pass through.
    """
    if p.status != "ok":
        return p
    out = p
    if readiness in ("amber", "red") and _progresses(rx, out):
        out = replace(_keep_last(rx, out), modifiers=(*out.modifiers, f"readiness:{readiness}"))
    if readiness == "red":
        sets = out.sets
        if rx.role in ("main", "secondary"):
            sets = max(out.sets - RED_SETS_REMOVE, min(RED_MIN_SETS, out.sets))
        out = replace(
            out,
            sets=sets,
            target_reps=out.target_reps[:sets],
            target_rir=out.target_rir + RED_RIR_ADD,
            modifiers=tuple(dict.fromkeys((*out.modifiers, "readiness:red"))),
        )
    if (
        interference
        and rx.role == "main"
        and rx.lower_body
        and out.weight_lb is not None
        and out.last_weight_lb is not None
        and out.weight_lb > out.last_weight_lb
    ):
        out = replace(_keep_last(rx, out), modifiers=(*out.modifiers, "endurance_interference"))
    return out
