"""Daily metrics sync against local Postgres with a fake Garmin client. No network.

Fixture dates are shifted: "today" in the recordings is 2025-10-05.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select

from spotter.db.schema import daily_metrics, sync_state
from spotter.sync.metrics import RANGE_DAYS, sync_metrics
from spotter.sync.sync import run_sync
from tests.test_sync import FakeClient, strength

FIXTURES = Path(__file__).parent / "fixtures" / "garmin" / "metrics"
TODAY, YESTERDAY = date(2025, 10, 5), date(2025, 10, 4)
STARTED = datetime(2025, 10, 5, 16, 0, tzinfo=UTC)
OLD = date(2025, 9, 30)  # outside the 3-day per-day window


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class FakeMetrics:
    """Recorded payloads for TODAY and YESTERDAY; one extra summary on OLD."""

    def __init__(self) -> None:
        self.day_calls: list[date] = []
        self.range_calls: list[tuple[date, date]] = []
        self.per_day = {
            TODAY: {
                "sleep": load("sleep_today"),
                "readiness": load("training_readiness_today"),
                "summary": load("daily_summary_today"),
            },
            YESTERDAY: {
                "sleep": load("sleep_yesterday"),
                "readiness": load("training_readiness_yesterday"),
                "summary": load("daily_summary_yesterday"),
            },
            OLD: {"summary": {**load("daily_summary_yesterday"), "restingHeartRate": 49}},
        }

    def _day(self, day: date, source: str, empty: Any) -> Any:
        return copy.deepcopy(self.per_day.get(day, {}).get(source, empty))

    def hrv_range(self, start: date, end: date) -> dict[str, Any]:
        self.range_calls.append((start, end))
        return load("hrv_range")

    def max_metrics_range(self, start: date, end: date) -> Any:
        return load("max_metrics_range")

    def body_composition_range(self, start: date, end: date) -> dict[str, Any]:
        return load("body_composition_range")

    def training_readiness(self, day: date) -> list[dict[str, Any]]:
        return self._day(day, "readiness", [])  # type: ignore[no-any-return]

    def sleep(self, day: date) -> dict[str, Any]:
        self.day_calls.append(day)
        return self._day(day, "sleep", {})  # type: ignore[no-any-return]

    def daily_summary(self, day: date) -> dict[str, Any]:
        return self._day(day, "summary", {})  # type: ignore[no-any-return]


def rows(db: Engine) -> dict[date, Any]:
    with db.connect() as conn:
        return {r.local_date: r for r in conn.execute(select(daily_metrics))}


def run(db: Engine, client: FakeMetrics, **kw: Any) -> Any:
    return sync_metrics(db, client, TODAY, STARTED, **kw)


def test_first_run_fetches_every_day(db: Engine) -> None:
    client = FakeMetrics()
    result = run(db, client)
    assert (result.days, result.day_fetches, result.partial) == (RANGE_DAYS, RANGE_DAYS, False)
    assert client.range_calls == [(TODAY - timedelta(days=RANGE_DAYS - 1), TODAY)]
    assert client.day_calls == [TODAY - timedelta(days=i) for i in range(RANGE_DAYS)]

    stored = rows(db)
    assert len(stored) == RANGE_DAYS
    today, yesterday = stored[TODAY], stored[YESTERDAY]
    assert (today.sleep_score, today.resting_hr, today.stress_avg) == (58, 55, None)
    assert (today.body_battery_max, today.body_battery_min, today.hrv_overnight_ms) == (36, 15, 52)
    assert (yesterday.sleep_score, yesterday.sleep_s, yesterday.hrv_overnight_ms) == (72, 27000, 44)
    assert (yesterday.resting_hr, yesterday.stress_avg) == (52, 31)
    assert yesterday.vo2max_run == Decimal("51.3")
    assert yesterday.hrv_status is None and yesterday.training_readiness is None  # onboarding
    assert stored[date(2025, 10, 3)].bodyweight_kg == Decimal("80.00")
    assert set(today.raw_json) == {"hrv", "max_metrics", "weight", "sleep", "readiness", "summary"}
    assert "sleepHeartRate" not in json.dumps(today.raw_json)

    with db.connect() as conn:
        state = conn.execute(select(sync_state).where(sync_state.c.source == "daily_metrics")).one()
    assert state.last_synced_at == STARTED
    assert state.cursor == {"synced_through": "2025-10-05"}


def test_next_run_refetches_only_recent_days_and_keeps_older_values(db: Engine) -> None:
    run(db, FakeMetrics())
    assert rows(db)[OLD].resting_hr == 49

    client = FakeMetrics()
    client.per_day[OLD]["summary"]["restingHeartRate"] = 60  # would show if OLD were re-read
    result = run(db, client)
    assert result.day_fetches == 3
    assert client.day_calls == [TODAY, YESTERDAY, date(2025, 10, 3)]
    old = rows(db)[OLD]
    assert old.resting_hr == 49  # range-only upsert left per-day columns alone
    assert {"summary", "hrv"} <= set(old.raw_json)  # raw_json merged per source


def test_wider_window_fetches_days_never_fetched(db: Engine) -> None:
    run(db, FakeMetrics())
    client = FakeMetrics()
    result = run(db, client, days=RANGE_DAYS + 6)
    assert result.days == RANGE_DAYS + 6
    assert result.day_fetches == 3 + 6
    assert len(rows(db)) == RANGE_DAYS + 6


def counter(fetches_allowed: int) -> tuple[Callable[[], float], float]:
    """A clock that passes the deadline after `fetches_allowed` budget checks."""
    ticks = iter(range(10_000))
    return (lambda: float(next(ticks))), float(fetches_allowed)


def test_budget_exhausted_writes_range_data_and_reports_partial(db: Engine) -> None:
    clock, deadline = counter(2)
    result = run(db, FakeMetrics(), deadline=deadline, clock=clock)
    assert (result.days, result.day_fetches, result.partial) == (RANGE_DAYS, 2, True)
    stored = rows(db)
    assert len(stored) == RANGE_DAYS
    assert stored[date(2025, 10, 3)].bodyweight_kg == Decimal("80.00")  # range data still written
    assert "sleep" not in stored[date(2025, 10, 3)].raw_json
    with db.connect() as conn:
        assert conn.execute(select(func.count()).select_from(sync_state)).scalar_one() == 0

    resumed = run(db, FakeMetrics())
    assert (resumed.day_fetches, resumed.partial) == (RANGE_DAYS, False)


def test_run_sync_includes_metrics(db: Engine) -> None:
    class Both(FakeClient, FakeMetrics):
        def __init__(self) -> None:
            FakeClient.__init__(self, [])
            FakeMetrics.__init__(self)

    now = datetime(2025, 10, 5, 16, 0, tzinfo=UTC)
    result = run_sync(db, Both(), now=lambda: now, metrics_days=5)
    assert (result.metric_days, result.metric_day_fetches, result.partial) == (5, 5, False)
    assert len(rows(db)) == 5


def test_metrics_skipped_when_activities_run_out_of_budget(db: Engine) -> None:
    class Both(FakeClient, FakeMetrics):
        def __init__(self) -> None:
            FakeClient.__init__(self, [strength(1, "2025-10-04 22:00:00")])
            FakeMetrics.__init__(self)

    now = datetime(2025, 10, 5, 16, 0, tzinfo=UTC)
    result = run_sync(db, Both(), now=lambda: now, budget_s=0)
    assert result.partial is True
    assert (result.metric_days, result.metric_day_fetches) == (0, 0)
    assert rows(db) == {}
