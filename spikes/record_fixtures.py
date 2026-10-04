"""Dump raw Garmin payloads to spikes/out/ (never committed).

    uv run --env-file .env.local python spikes/record_fixtures.py [days]
    uv run --env-file .env.local python spikes/record_fixtures.py --metrics

Default: the activity list for the last N days (1 call).
--metrics: every daily-metric call Spotter uses (SPEC 8.2).
Range calls cover the last 14 days. Per-day calls cover yesterday and today. 9 calls.

Then anonymize: uv run spikes/anonymize_fixture.py --list | --metrics
Uses the token stored in Postgres and saves a refresh.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from spotter.db.session import get_engine
from spotter.garmin.session import garmin_session

OUT = Path(__file__).resolve().parent / "out" / "activities_list.json"
METRICS_DIR = OUT.parent / "metrics"
RANGE_DAYS = 14
RANGE_CALLS = ("hrv_range", "max_metrics_range", "body_composition_range")
DAY_CALLS = ("training_readiness", "sleep", "daily_summary")


def record_metrics() -> None:
    today = datetime.now(ZoneInfo("America/Toronto")).date()
    days = {"yesterday": today - timedelta(days=1), "today": today}
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    with garmin_session(get_engine()) as session:
        c = session.client
        for name in RANGE_CALLS:
            data = getattr(c, name)(today - timedelta(days=RANGE_DAYS - 1), today)
            (METRICS_DIR / f"{name}.json").write_text(json.dumps(data, indent=2))
            print(f"{name}: {_shape(data)}")
        for label, day in days.items():
            for name in DAY_CALLS:
                data = getattr(c, name)(day)
                (METRICS_DIR / f"{name}_{label}.json").write_text(json.dumps(data, indent=2))
                print(f"{name} {label}: {_shape(data)}")
    print(f"Wrote {METRICS_DIR}. tokens refreshed: {session.refreshed}")


def _shape(data: object) -> str:
    """Size only. Never print payload values."""
    if isinstance(data, list):
        return f"list[{len(data)}]"
    if isinstance(data, dict):
        return f"dict[{len(data)} keys]"
    return type(data).__name__


def main() -> None:
    if sys.argv[1:] == ["--metrics"]:
        record_metrics()
        return
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    end = date.today()
    with garmin_session(get_engine()) as session:
        listing = session.client.activities_by_date(end - timedelta(days=days), end)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(listing, indent=2))
    types = Counter(a["activityType"]["typeKey"] for a in listing)
    print(f"{len(listing)} activities in {days} days: {dict(types)}")
    print(f"tokens refreshed: {session.refreshed}")


if __name__ == "__main__":
    main()
