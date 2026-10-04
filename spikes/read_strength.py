# /// script
# requires-python = ">=3.12"
# dependencies = ["garminconnect[workout]==0.3.17"]
# ///
"""Phase 0 check 2: read the last strength activity and its exercise sets.

    uv run spikes/read_strength.py [--activity-id N] [--days 90]

Raw payloads go to spikes/out/ (gitignored). Compare the printed lb values
with what was logged on the watch. Run anonymize_fixture.py before committing
anything to tests/fixtures/.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from typing import Any

from _garmin import close_client, kg_to_lb, load_client, write_out


def find_workout_keys(obj: Any, path: str = "") -> list[str]:
    """Paths of any key mentioning 'workout' (open item: activity -> workout id link)."""
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if "workout" in k.lower():
                hits.append(f"{p} = {v!r}"[:120])
            hits += find_workout_keys(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:3]):
            hits += find_workout_keys(v, f"{path}[{i}]")
    return hits


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activity-id", type=int)
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    garmin = load_client()
    start = (date.today() - timedelta(days=args.days)).isoformat()
    activities = garmin.get_activities_by_date(start, date.today().isoformat())
    strength = [a for a in activities if a.get("activityType", {}).get("typeKey") == "strength_training"]
    print(f"{len(activities)} activities since {start}, {len(strength)} strength")

    if args.activity_id:
        activity = next((a for a in activities if a.get("activityId") == args.activity_id), None)
        activity_id = args.activity_id
    else:
        if not strength:
            raise SystemExit("No strength activity in range. Try --days 365.")
        activity = max(strength, key=lambda a: a.get("startTimeGMT", ""))
        activity_id = activity["activityId"]

    sets = garmin.get_activity_exercise_sets(activity_id)
    full = garmin.get_activity(str(activity_id))
    close_client(garmin)

    print(f"Raw activity: {write_out(f'activity_{activity_id}.json', activity)}")
    print(f"Raw full:     {write_out(f'activity_full_{activity_id}.json', full)}")
    print(f"Raw sets:     {write_out(f'exercise_sets_{activity_id}.json', sets)}")
    if activity:
        print(f"Activity: {activity.get('activityName')!r} {activity.get('startTimeLocal')}")

    rows = sets.get("exerciseSets") or []
    print(f"\n{len(rows)} set rows. Top-level keys: {sorted(sets)}")
    print(f"Set row keys: {sorted(rows[0]) if rows else []}\n")
    print(f"{'#':>3} {'type':<7} {'category':<16} {'name':<26} {'reps':>4} {'raw wt':>10} {'kg':>8} {'lb':>7}")
    for i, s in enumerate(rows):
        ex = (s.get("exercises") or [{}])[0]
        raw = s.get("weight")
        # Assumption to verify: Garmin returns set weight in grams.
        kg = raw / 1000 if isinstance(raw, (int, float)) else None
        print(
            f"{i:>3} {str(s.get('setType', ''))[:7]:<7} {str(ex.get('category', ''))[:16]:<16} "
            f"{str(ex.get('name', ''))[:26]:<26} {str(s.get('repetitionCount', '')):>4} "
            f"{str(raw):>10} {'' if kg is None else f'{kg:.3f}':>8} "
            f"{'' if kg is None else kg_to_lb(kg):>7}"
        )

    print("\nWorkout-id fields (list summary, then full activity):")
    for line in find_workout_keys(activity) + find_workout_keys(full) or ["(none found)"]:
        print(f"  {line}")


if __name__ == "__main__":
    main()
