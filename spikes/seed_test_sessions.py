"""Phase 2 exit check: seed 3 private test strength sessions in Garmin Connect.

    uv run --env-file <.env.local> python spikes/seed_test_sessions.py --dry-run
    uv run --env-file <.env.local> python spikes/seed_test_sessions.py --only 1
    uv run --env-file <.env.local> python spikes/seed_test_sessions.py
    uv run --env-file <.env.local> python spikes/seed_test_sessions.py --delete

Throwaway. Uses the token stored in Postgres (saves a refresh). Writes created
ids to spikes/out/seeded_sessions.json. --delete removes them from Garmin and
from the `activities` table (CASCADE clears sets and stats).
Expected stats per session are in docs/decisions.md.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from garminconnect import exercises as garmin_exercises
from sqlalchemy import delete

from spotter.db.schema import activities
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

# (exercise, lb or None for bodyweight, reps)
SESSIONS: dict[int, tuple[str, list[tuple[tuple[str, str], float | None, int]]]] = {
    1: (
        "2026-09-28",
        [
            (BENCH, 95, 8),  # warm-up: 51 % of 185
            (BENCH, 185, 5),
            (BENCH, 185, 5),
            (BENCH, 185, 4),
            (SQUAT, 135, 5),  # exactly 60 % of 225: working
            (SQUAT, 225, 5),
            (SQUAT, 225, 5),
            (PULL_UP, None, 8),
            (PULL_UP, None, 8),
            (PULL_UP, None, 6),
        ],
    ),
    2: (
        "2026-09-30",
        [
            (BENCH, 190, 5),
            (BENCH, 190, 5),
            (BENCH, 190, 5),
            (BENCH, 190, 0),  # 0-rep set: ignored
            (DB_ROW, 60, 12),  # over 10 reps: no e1RM
            (DB_ROW, 60, 12),
            (DB_ROW, 60, 10),
        ],
    ),
    3: (
        "2026-10-02",
        [
            (SQUAT, 135, 5),  # warm-up: 55 % of 245
            (SQUAT, 245, 5),
            (SQUAT, 245, 5),
            (SQUAT, 245, 3),
            (DEADLIFT, 135, 5),  # warm-up: 43 % of 315
            (DEADLIFT, 225, 3),  # 71 %: working
            (DEADLIFT, 315, 5),
        ],
    ),
}


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
            out.append(_set(len(out), "REST", t, REST_S, exercises=[], repetitionCount=None, weight=None))
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


def load_seeded() -> dict[str, int]:
    return json.loads(OUT.read_text()) if OUT.exists() else {}


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
            OUT.parent.mkdir(exist_ok=True)
            OUT.write_text(json.dumps(seeded, indent=2))
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
    with engine.begin() as conn:
        gone = conn.execute(
            delete(activities).where(activities.c.garmin_activity_id.in_(list(seeded.values())))
        ).rowcount
    print(f"deleted {gone} rows from activities")
    OUT.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", type=int, choices=sorted(SESSIONS))
    parser.add_argument("--delete", action="store_true")
    args = parser.parse_args()
    if args.delete:
        remove()
    else:
        seed([args.only] if args.only else sorted(SESSIONS), args.dry_run)


if __name__ == "__main__":
    main()
