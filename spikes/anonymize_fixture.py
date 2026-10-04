# /// script
# requires-python = ">=3.12"
# ///
"""Anonymize raw Garmin dumps from spikes/out/ into tests/fixtures/garmin/.

    uv run spikes/anonymize_fixture.py <activity_id>
    uv run spikes/anonymize_fixture.py --list   # from spikes/out/activities_list.json
    uv run spikes/anonymize_fixture.py --metrics  # from spikes/out/metrics/ to fixtures/garmin/metrics/

- Drops keys that identify the person, device or place.
- Replaces ids with stable fakes.
- Shifts dates and epoch-ms timestamps back 364 days (keeps weekdays).
Prints every remaining string value so a human can review before committing.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "spikes" / "out"
FIXTURES = ROOT / "tests" / "fixtures" / "garmin"

SHIFT_DAYS = 364
DROP_SUBSTRINGS = (
    "latitude", "longitude", "location", "gps", "polyline", "displayname", "fullname", "username",
    "profileimage", "owner", "serial", "deviceid", "devicename", "unitid", "manufacturer",
    "email", "address", "city", "country", "timezone",
    "userroles", "installationid", "deviceversionpk",
)
ID_KEYS = {"activityid": 900000001, "userprofileid": 1, "userprofilepk": 1, "workoutid": 800000001,
          "associatedworkoutid": 800000001}
FAKE_UUID = "00000000-0000-4000-8000-000000000001"
# Any other user or profile id key (userId, userProfilePK, profileId, ...).
USER_ID_KEY = re.compile(r"^(user\w*|profile)(id|pk)$")
FAKE_USER_ID = 1
# Activity names are free text the athlete typed (event names, places).
FAKE_ACTIVITY_NAME = "Activity"
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def shift_date(m: re.Match[str]) -> str:
    return (date.fromisoformat(m.group(1)) - timedelta(days=SHIFT_DAYS)).isoformat()


def clean(obj: Any, key: str = "") -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = k.lower()
            if any(s in kl for s in DROP_SUBSTRINGS):
                continue
            if kl in ID_KEYS and v is not None:
                out[k] = ID_KEYS[kl]
                continue
            if USER_ID_KEY.match(kl) and v is not None:
                out[k] = FAKE_USER_ID
                continue
            if kl == "activityname" and isinstance(v, str):
                out[k] = FAKE_ACTIVITY_NAME
                continue
            if "uuid" in kl and v is not None:
                out[k] = clean(v, kl) if isinstance(v, dict) else FAKE_UUID
                continue
            out[k] = clean(v, kl)
        return out
    if isinstance(obj, list):
        return [clean(v, key) for v in obj]
    if isinstance(obj, str):
        if key == "uuid":
            return FAKE_UUID
        return DATE_RE.sub(shift_date, obj)
    if isinstance(obj, int) and not isinstance(obj, bool) and obj > 10**12 and ("time" in key or "date" in key):
        return obj - SHIFT_DAYS * 86_400_000
    return obj


def real_ids(obj: Any) -> set[int]:
    """Every user, profile or activity id in a raw payload, to prove none survives."""
    found: set[int] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = k.lower()
            if (kl in ID_KEYS or USER_ID_KEY.match(kl)) and isinstance(v, int) and v > 1000:
                found.add(v)
            found |= real_ids(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= real_ids(v)
    return found


def assert_no_ids(raw: Any, cleaned: Any, name: str) -> None:
    text = json.dumps(cleaned)
    leaked = [i for i in real_ids(raw) if re.search(rf"\b{i}\b", text)]
    if leaked:
        raise SystemExit(f"{name}: {len(leaked)} real id(s) survived anonymization. Not written.")


def strings(obj: Any, path: str = "") -> list[str]:
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in strings(v, f"{path}.{k}")]
    if isinstance(obj, list):
        return [s for i, v in enumerate(obj) for s in strings(v, f"{path}[{i}]")]
    return [f"{path} = {obj!r}"] if isinstance(obj, str) else []


LIST_TYPES = ("running", "cycling")


def from_list() -> None:
    """First activity of each LIST_TYPES type -> <type>_activity_summary.json. Names replaced."""
    listing = json.loads((OUT / "activities_list.json").read_text())
    written = []
    for type_key in LIST_TYPES:
        found = next((a for a in listing if a["activityType"]["typeKey"] == type_key), None)
        if found is None:
            print(f"no {type_key} activity in the list, skipping")
            continue
        data = clean({**found, "activityName": type_key.title(), "description": None})
        dst = f"{type_key}_activity_summary.json"
        (FIXTURES / dst).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        written.append(dst)
        print(f"\n== {dst}: review remaining strings ==")
        for line in sorted(set(strings(data))):
            print(f"  {line[:140]}")
    print(f"\nWrote {written}. Review the strings above before committing.")


# Metrics fixtures publish no real health value (the repo is public, decisions.md):
# every number becomes 0 except timestamps, time series are emptied, and the
# fields Spotter maps get the fake values below. Real structure, fake numbers.
TIME_SERIES = {
    "sleepmovement", "sleepheartrate", "sleepstress", "sleepbodybattery", "hrvdata",
    "sleeplevels", "sleeprestlessmoments", "breathingdisruptiondata",
    "wellnessepochrespirationdatadtolist", "wellnessepochrespirationaverageslist",
    "bodybatteryvaluesarray", "bodybatteryactivityeventlist", "bodybatteryactivityevent",
}
KEEP_NUMBER = re.compile(r"time|date|timestamp")
EPOCH_MIN = 10**9  # timestamps (s or ms) stay; durations under time-ish keys do not
FAKES: dict[str, list[tuple[str, Any]]] = {
    "hrv_range.json": [("hrvSummaries.0.lastNightAvg", 44), ("hrvSummaries.1.lastNightAvg", 52),
                       ("hrvSummaries.*.lastNight5MinHigh", 70)],
    "max_metrics_range.json": [("*.generic.vo2MaxPreciseValue", 51.3),
                               ("*.generic.vo2MaxValue", 51.0)],
    "body_composition_range.json": [("dateWeightList.*.weight", 80000.0),
                                    ("totalAverage.weight", 80000.0)],
    "sleep_yesterday.json": [("dailySleepDTO.sleepScores.overall.value", 72),
                             ("dailySleepDTO.sleepTimeSeconds", 27000)],
    "sleep_today.json": [("dailySleepDTO.sleepScores.overall.value", 58),
                         ("dailySleepDTO.sleepTimeSeconds", 23400)],
    "daily_summary_yesterday.json": [("restingHeartRate", 52), ("averageStressLevel", 31),
                                     ("bodyBatteryHighestValue", 88),
                                     ("bodyBatteryLowestValue", 22)],
    "daily_summary_today.json": [("restingHeartRate", 55), ("averageStressLevel", -1),
                                 ("bodyBatteryHighestValue", 36),
                                 ("bodyBatteryLowestValue", 15)],
}


def zero_numbers(obj: Any, key: str = "") -> Any:
    if isinstance(obj, dict):
        return {k: [] if k.lower() in TIME_SERIES and isinstance(v, list) else zero_numbers(v, k.lower())
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [zero_numbers(v, key) for v in obj]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        is_epoch = KEEP_NUMBER.search(key) and abs(obj) >= EPOCH_MIN
        return obj if is_epoch else 0
    return obj


def set_path(obj: Any, path: str, value: Any) -> int:
    """Set `value` at a dotted path; `*` matches every list item. Returns the number set."""
    head, _, rest = path.partition(".")
    items = (list(enumerate(obj)) if head == "*" else [(int(head), obj[int(head)])]
             if isinstance(obj, list) else [(head, obj.get(head))])
    count = 0
    for k, child in items:
        if not rest:
            if child is not None or head != "*":
                obj[k] = value
                count += 1
        elif isinstance(child, (dict, list)):
            count += set_path(child, rest, value)
    return count


def fake_metrics(name: str, data: Any) -> Any:
    data = zero_numbers(data)
    for path, value in FAKES.get(name, []):
        if set_path(data, path, value) == 0:
            raise SystemExit(f"{name}: fake path {path} matched nothing")
    return data


def from_metrics() -> None:
    """Every file in spikes/out/metrics/ -> tests/fixtures/garmin/metrics/, same name."""
    dst_dir = FIXTURES / "metrics"
    dst_dir.mkdir(parents=True, exist_ok=True)
    srcs = sorted((OUT / "metrics").glob("*.json"))
    for src in srcs:
        raw = json.loads(src.read_text())
        data = fake_metrics(src.name, clean(raw))
        assert_no_ids(raw, data, src.name)
        (dst_dir / src.name).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(f"\n== metrics/{src.name}: review remaining strings ==")
        for line in sorted(set(strings(data))):
            print(f"  {line[:140]}")
    print(f"\nWrote {len(srcs)} fixtures to {dst_dir}. Review the strings above before committing.")


def main() -> None:
    if sys.argv[1:] == ["--list"]:
        from_list()
        return
    if sys.argv[1:] == ["--metrics"]:
        from_metrics()
        return
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    aid = sys.argv[1]
    FIXTURES.mkdir(parents=True, exist_ok=True)
    pairs = {
        f"activity_{aid}.json": "strength_activity_summary.json",
        f"activity_full_{aid}.json": "strength_activity.json",
        f"exercise_sets_{aid}.json": "exercise_sets.json",
    }
    for src, dst in pairs.items():
        data = clean(json.loads((OUT / src).read_text()))
        (FIXTURES / dst).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(f"\n== {dst}: review remaining strings ==")
        for line in sorted(set(strings(data))):
            print(f"  {line[:140]}")
    print(f"\nWrote {len(pairs)} fixtures to {FIXTURES}. Review the strings above before committing.")


if __name__ == "__main__":
    main()
