# /// script
# requires-python = ">=3.12"
# ///
"""Anonymize raw Garmin dumps from spikes/out/ into tests/fixtures/garmin/.

    uv run spikes/anonymize_fixture.py <activity_id>

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


def strings(obj: Any, path: str = "") -> list[str]:
    if isinstance(obj, dict):
        return [s for k, v in obj.items() for s in strings(v, f"{path}.{k}")]
    if isinstance(obj, list):
        return [s for i, v in enumerate(obj) for s in strings(v, f"{path}[{i}]")]
    return [f"{path} = {obj!r}"] if isinstance(obj, str) else []


def main() -> None:
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
