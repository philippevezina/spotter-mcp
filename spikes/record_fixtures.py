"""Dump the raw activity list for the last N days to spikes/out/ (never committed).

    uv run --env-file <.env.local> python spikes/record_fixtures.py [days]

Then anonymize: uv run spikes/anonymize_fixture.py --list
Uses the token stored in Postgres and saves a refresh.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

from spotter.db.session import get_engine
from spotter.garmin.session import garmin_session

OUT = Path(__file__).resolve().parent / "out" / "activities_list.json"


def main() -> None:
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
