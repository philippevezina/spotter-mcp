"""Exit checks: seed private test strength sessions in Garmin Connect.

    uv run --env-file <.env.local> python spikes/seed_test_sessions.py --dry-run
    uv run --env-file <.env.local> python spikes/seed_test_sessions.py --only 1
    uv run --env-file <.env.local> python spikes/seed_test_sessions.py
    uv run --env-file <.env.local> python spikes/seed_test_sessions.py --delete
    uv run --env-file <.env.local> python spikes/seed_test_sessions.py --set phase4 ...
    uv run --env-file <.env.local> python spikes/seed_test_sessions.py --delete-plan <id>

Throwaway. Uses the token stored in Postgres (saves a refresh). Writes created
ids to spikes/out/seeded_sessions[_<set>].json. --delete removes them from Garmin and
from the `activities` table (CASCADE clears sets and stats), after unlinking any
scheduled session that points at them. --delete-plan removes a test plan with its
days, schedule, adjustments and notes.
Expected values are in docs/decisions.md ("Phase 3 test sessions", "Phase 4 test sessions").
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from garminconnect import exercises as garmin_exercises
from sqlalchemy import delete, select, update

from spotter.db.schema import (
    activities,
    adjustments,
    plan_days,
    plans,
    scheduled_sessions,
    session_notes,
)
from spotter.db.session import get_engine
from spotter.garmin.session import garmin_session

OUT = Path(__file__).resolve().parent / "out" / "seeded_sessions.json"
TZ = ZoneInfo("America/Toronto")
GRAMS_PER_LB = 453.59237
SET_S, REST_S = 40.0, 120.0

BENCH = ("BENCH_PRESS", "BARBELL_BENCH_PRESS")
SQUAT = ("SQUAT", "BARBELL_BACK_SQUAT")
PULL_UP = ("PULL_UP", "PULL_UP")
DB_ROW = ("ROW", "DUMBBELL_ROW")
DEADLIFT = ("DEADLIFT", "BARBELL_DEADLIFT")
OHP = ("SHOULDER_PRESS", "OVERHEAD_BARBELL_PRESS")

Set = tuple[tuple[str, str], float | None, int]  # (exercise, lb or None for bodyweight, reps)
Session = tuple[str, list[Set]]


def upper(
    day: str, bench: float, bench_last: int, row: float, row_reps: list[int], pull: list[int]
) -> Session:
    """Bench (95 lb warm-up, then 3 working sets), dumbbell row, pull-ups."""
    sets: list[Set] = [(BENCH, 95, 8), (BENCH, bench, 5), (BENCH, bench, 5)]
    sets += [(BENCH, bench, bench_last)]
    sets += [(DB_ROW, row, r) for r in row_reps]
    sets += [(PULL_UP, None, r) for r in pull]
    return day, sets


def lower(day: str, squat: float, deadlift: float) -> Session:
    """Squat (95 lb warm-up, 3x5), deadlift (135 lb warm-up, 2x5). Warm-ups stay under 60 %."""
    sets: list[Set] = [(SQUAT, 95, 5)] + [(SQUAT, squat, 5)] * 3
    sets += [(DEADLIFT, 135, 5)] + [(DEADLIFT, deadlift, 5)] * 2
    return day, sets


# Phase 3 exit check: 8 sessions over 4 weeks. All stay inside the 28-day window
# when the check runs between 2026-10-03 and 2026-10-06.
PHASE3: dict[int, Session] = {
    1: upper("2026-09-09", 175, 5, 55, [12, 12, 12], [8, 8, 6]),
    2: lower("2026-09-12", 205, 255),
    3: upper("2026-09-16", 180, 5, 60, [12, 12, 12], [8, 8, 7]),
    4: lower("2026-09-19", 215, 275),
    5: upper("2026-09-23", 185, 5, 60, [12, 12, 10], [9, 8, 7]),
    6: lower("2026-09-26", 225, 295),
    7: upper("2026-09-30", 185, 4, 65, [10, 10, 10], [9, 9, 8]),
    8: lower("2026-10-03", 235, 315),
}


def p4_upper(day: str, last_week: bool) -> Session:
    """OHP misses 115 x (5,4,4) every week; bench and row reach scenario 2 and 3 in week 3."""
    sets: list[Set] = [(OHP, 45, 5)] + [(OHP, 115, r) for r in (5, 4, 4)]
    sets += [(BENCH, 95, 8)] + [(BENCH, 185, r) for r in ((8, 8, 8) if last_week else (7, 7, 6))]
    sets += [(DB_ROW, 60, r) for r in ((10, 10, 9) if last_week else (9, 9, 8))]
    return day, sets


def p4_lower(day: str, squat: float) -> Session:
    return day, [(SQUAT, 95, 5)] + [(SQUAT, squat, 5)] * 3


# Phase 4 exit check: a deadlift before the plan, then weeks 1 to 3 of a plan that
# starts on 2026-09-14 (Upper on Tuesday, Lower on Thursday). Run the check on 2026-10-05.
PHASE4: dict[int, Session] = {
    1: ("2026-09-07", [(DEADLIFT, 135, 5), (DEADLIFT, 315, 5), (DEADLIFT, 315, 5)]),
    2: p4_upper("2026-09-15", False),
    3: p4_lower("2026-09-17", 205),
    4: p4_upper("2026-09-22", False),
    5: p4_lower("2026-09-24", 215),
    6: p4_upper("2026-09-29", True),
    7: p4_lower("2026-10-01", 225),
}

SETS = {"phase3": PHASE3, "phase4": PHASE4}
SESSIONS = PHASE3  # selected by --set in main()


def check_catalog() -> None:
    known = {(e["category"], e["exercise"]) for e in garmin_exercises.EXERCISES}
    used = {ex for _, sets in SESSIONS.values() for ex, _, _ in sets}
    missing = used - known
    if missing:
        raise SystemExit(f"not in Garmin catalog: {sorted(missing)}")


def start_local(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T18:00:00").replace(tzinfo=TZ)


def _set(index: int, set_type: str, start: datetime, duration: float, **kw: Any) -> dict[str, Any]:
    return {
        "messageIndex": index,
        "setType": set_type,
        "startTime": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.0"),
        "duration": duration,
        "wktStepIndex": None,
        **kw,
    }


def sets_payload(activity_id: int, number: int) -> dict[str, Any]:
    day, sets = SESSIONS[number]
    t = start_local(day)
    out: list[dict[str, Any]] = []
    for i, ((category, name), lb, reps) in enumerate(sets):
        if i:
            out.append(
                _set(len(out), "REST", t, REST_S, exercises=[], repetitionCount=None, weight=None)
            )
            t += timedelta(seconds=REST_S)
        out.append(
            _set(
                len(out),
                "ACTIVE",
                t,
                SET_S,
                exercises=[{"category": category, "name": name, "probability": 100.0}],
                repetitionCount=reps,
                weight=None if lb is None else float(round(lb * GRAMS_PER_LB)),
            )
        )
        t += timedelta(seconds=SET_S)
    return {"activityId": activity_id, "exerciseSets": out}


def out_path() -> Path:
    return OUT if SESSIONS is PHASE3 else OUT.with_name("seeded_sessions_phase4.json")


def load_seeded() -> dict[str, int]:
    path = out_path()
    return json.loads(path.read_text()) if path.exists() else {}


def seed(numbers: list[int], dry_run: bool) -> None:
    check_catalog()
    if dry_run:
        for n in numbers:
            print(f"== session {n} ({SESSIONS[n][0]})")
            print(json.dumps(sets_payload(0, n), indent=1)[:1500])
        return
    seeded = load_seeded()
    engine = get_engine()
    with garmin_session(engine) as session:
        api = session.client._api  # spike only: library calls outside the adapter
        for n in numbers:
            if str(n) in seeded:
                print(f"session {n}: already seeded as {seeded[str(n)]}, skipping")
                continue
            day, _ = SESSIONS[n]
            created = api.create_manual_activity(
                start_datetime=start_local(day).strftime("%Y-%m-%dT%H:%M:%S.000"),
                time_zone="America/Toronto",
                type_key="strength_training",
                distance_km=0,
                duration_min=45,
                activity_name=f"Spotter test {n}",
            )
            data = created.json() if hasattr(created, "json") else created
            activity_id = int(data["activityId"])
            seeded[str(n)] = activity_id
            out_path().parent.mkdir(exist_ok=True)
            out_path().write_text(json.dumps(seeded, indent=2))
            print(f"session {n}: created activity {activity_id}")
            api.set_activity_exercise_sets(activity_id, sets_payload(activity_id, n))
            back = api.get_activity_exercise_sets(activity_id)
            active = [s for s in back.get("exerciseSets") or [] if s.get("setType") == "ACTIVE"]
            print(f"session {n}: sets stored, {len(active)} active sets read back")
            for s in active:
                ex = (s.get("exercises") or [{}])[0]
                w = s.get("weight")
                lb = "BW" if w is None else f"{w / GRAMS_PER_LB:.1f} lb"
                print(f"  {ex.get('category')}/{ex.get('name')}: {s.get('repetitionCount')} x {lb}")
    print(f"tokens refreshed: {session.refreshed}")


def remove() -> None:
    seeded = load_seeded()
    if not seeded:
        print("nothing seeded")
        return
    engine = get_engine()
    with garmin_session(engine) as session:
        api = session.client._api
        for n, activity_id in sorted(seeded.items()):
            api.delete_activity(str(activity_id))
            print(f"session {n}: deleted Garmin activity {activity_id}")
    ids = list(seeded.values())
    with engine.begin() as conn:
        unlinked = conn.execute(
            update(scheduled_sessions)
            .where(scheduled_sessions.c.activity_id.in_(ids))
            .values(activity_id=None)
        ).rowcount
        gone = conn.execute(
            delete(activities).where(activities.c.garmin_activity_id.in_(ids))
        ).rowcount
    print(f"unlinked {unlinked} scheduled sessions, deleted {gone} rows from activities")
    out_path().unlink()


def remove_plan(plan_id: int) -> None:
    """Delete a test plan. Days, exercises and sessions cascade; notes and adjustments do not."""
    engine = get_engine()
    with engine.begin() as conn:
        name = conn.execute(select(plans.c.name).where(plans.c.id == plan_id)).scalar()
        if name is None:
            raise SystemExit(f"no plan {plan_id}")
        sessions = (
            select(scheduled_sessions.c.id)
            .join(plan_days, plan_days.c.id == scheduled_sessions.c.plan_day_id)
            .where(plan_days.c.plan_id == plan_id)
        )
        notes = conn.execute(
            delete(session_notes).where(session_notes.c.scheduled_session_id.in_(sessions))
        ).rowcount
        adj = conn.execute(delete(adjustments).where(adjustments.c.plan_id == plan_id)).rowcount
        conn.execute(delete(plans).where(plans.c.id == plan_id))
    print(f"deleted plan {plan_id} ({name}), {notes} notes, {adj} adjustments")


def main() -> None:
    global SESSIONS
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", choices=sorted(SETS), default="phase3")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", type=int)
    parser.add_argument("--delete", action="store_true")
    parser.add_argument("--delete-plan", type=int, metavar="PLAN_ID")
    args = parser.parse_args()
    SESSIONS = SETS[args.set]
    if args.only is not None and args.only not in SESSIONS:
        parser.error(f"--only must be one of {sorted(SESSIONS)}")
    if args.delete_plan is not None:
        remove_plan(args.delete_plan)
    elif args.delete:
        remove()
    else:
        seed([args.only] if args.only else sorted(SESSIONS), args.dry_run)


if __name__ == "__main__":
    main()
