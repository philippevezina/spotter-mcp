"""Client wrapper tests with a fake garminconnect.Garmin. No network."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import ClassVar

import garminconnect
import pytest

from spotter.garmin import client
from spotter.garmin.errors import (
    GarminAuthExpired,
    GarminNotFound,
    GarminRateLimited,
    GarminUnavailable,
)

TOKEN = json.dumps({"di_token": "t1", "di_refresh_token": "r1", "di_client_id": "c"})
REFRESHED = TOKEN.replace("t1", "t2")


class FakeInner:
    def __init__(self) -> None:
        self.tokens = ""

    def dumps(self) -> str:
        return self.tokens


class FakeGarmin:
    login_error: Exception | None = None
    read_error: Exception | None = None
    refresh_on_login = False
    instances: ClassVar[list[FakeGarmin]] = []

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        prompt_mfa: Callable[[], str] | None = None,
    ) -> None:
        self.email, self.password, self.prompt_mfa = email, password, prompt_mfa
        self.client = FakeInner()
        self.login_calls: list[str | None] = []
        self.metric_calls: list[tuple[str, tuple[str, ...]]] = []
        type(self).instances.append(self)

    def login(self, tokenstore: str | None = None) -> tuple[None, None]:
        self.login_calls.append(tokenstore)
        if self.login_error:
            raise self.login_error
        if tokenstore is None:
            assert self.prompt_mfa is not None
            self.client.tokens = json.dumps({**json.loads(TOKEN), "mfa": self.prompt_mfa()})
        else:
            self.client.tokens = REFRESHED if self.refresh_on_login else tokenstore
        return None, None

    def get_unit_system(self) -> str:
        if self.read_error:
            raise self.read_error
        return "statute_us"

    def get_activities_by_date(self, startdate: str, enddate: str) -> list[dict[str, object]]:
        if self.read_error:
            raise self.read_error
        return [{"activityId": 1, "range": [startdate, enddate]}]

    def get_activity_exercise_sets(self, activity_id: int) -> dict[str, object]:
        return {"activityId": activity_id, "exerciseSets": []}

    # Daily metric reads: record (method, args) and return `metric_result`.
    metric_result: object = None

    def _metric(self, name: str, *args: str) -> object:
        self.metric_calls.append((name, args))
        return self.metric_result

    def get_hrv_data_range(self, start: str, end: str) -> object:
        return self._metric("get_hrv_data_range", start, end)

    def get_max_metrics_range(self, start: str, end: str) -> object:
        return self._metric("get_max_metrics_range", start, end)

    def get_body_composition(self, startdate: str, enddate: str) -> object:
        return self._metric("get_body_composition", startdate, enddate)

    def get_training_readiness(self, cdate: str) -> object:
        return self._metric("get_training_readiness", cdate)

    def get_sleep_data(self, cdate: str) -> object:
        return self._metric("get_sleep_data", cdate)

    def get_user_summary(self, cdate: str) -> object:
        return self._metric("get_user_summary", cdate)

    # Workout writes: record (method, args); `write_error` raises instead.
    write_error: Exception | None = None

    def _write(self, name: str, *args: object) -> object:
        self.metric_calls.append((name, tuple(str(a) for a in args)))
        if self.write_error:
            raise self.write_error
        return {"workoutId": 77, "workoutScheduleId": 88}

    def upload_workout(self, payload: dict[str, object]) -> object:
        return self._write("upload_workout", payload["workoutName"])

    def update_workout(self, workout_id: int, payload: dict[str, object]) -> object:
        return self._write("update_workout", workout_id, payload["workoutName"])

    def schedule_workout(self, workout_id: int, date_str: str) -> object:
        return self._write("schedule_workout", workout_id, date_str)

    def unschedule_workout(self, schedule_id: int) -> object:
        return self._write("unschedule_workout", schedule_id)

    def delete_workout(self, workout_id: int) -> object:
        return self._write("delete_workout", workout_id)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> type[FakeGarmin]:
    class Fake(FakeGarmin):
        instances: ClassVar[list[FakeGarmin]] = []

    monkeypatch.setattr(garminconnect, "Garmin", Fake)
    return Fake


def test_login_with_credentials_returns_token_json(fake: type[FakeGarmin]) -> None:
    dumped = client.login_with_credentials("a@b.c", "pw", prompt_mfa=lambda: "123456")
    assert json.loads(dumped)["mfa"] == "123456"
    assert fake.instances[0].login_calls == [None]


def test_login_429_is_rate_limited(fake: type[FakeGarmin]) -> None:
    fake.login_error = garminconnect.GarminConnectTooManyRequestsError("429")
    with pytest.raises(GarminRateLimited, match="do not retry"):
        client.login_with_credentials("a@b.c", "pw", prompt_mfa=lambda: "")


def test_from_tokens_never_uses_credentials(fake: type[FakeGarmin]) -> None:
    session = client.GarminClient.from_tokens(TOKEN)
    inst = fake.instances[0]
    assert (inst.email, inst.password) == (None, None)
    assert inst.login_calls == [TOKEN]
    assert session.dumps() == TOKEN


def test_from_tokens_reports_refresh(fake: type[FakeGarmin]) -> None:
    fake.refresh_on_login = True
    assert client.GarminClient.from_tokens(TOKEN).dumps() == REFRESHED


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (garminconnect.GarminConnectAuthenticationError("401"), GarminAuthExpired),
        (garminconnect.GarminConnectTooManyRequestsError("429"), GarminRateLimited),
    ],
)
def test_from_tokens_maps_errors(
    fake: type[FakeGarmin], error: Exception, expected: type[Exception]
) -> None:
    fake.login_error = error
    with pytest.raises(expected):
        client.GarminClient.from_tokens(TOKEN)


def test_expired_message_tells_how_to_recover(fake: type[FakeGarmin]) -> None:
    fake.login_error = garminconnect.GarminConnectAuthenticationError("401")
    with pytest.raises(GarminAuthExpired, match="spotter bootstrap-login"):
        client.GarminClient.from_tokens(TOKEN)


def test_check_maps_errors(fake: type[FakeGarmin]) -> None:
    session = client.GarminClient.from_tokens(TOKEN)
    session.check()
    fake.read_error = garminconnect.GarminConnectAuthenticationError("401")
    with pytest.raises(GarminAuthExpired):
        session.check()


def test_reads_pass_iso_dates(fake: type[FakeGarmin]) -> None:
    session = client.GarminClient.from_tokens(TOKEN)
    acts = session.activities_by_date(date(2026, 1, 1), date(2026, 10, 4))
    assert acts == [{"activityId": 1, "range": ["2026-01-01", "2026-10-04"]}]
    assert session.exercise_sets(7) == {"activityId": 7, "exerciseSets": []}


def test_connection_error_is_unavailable(fake: type[FakeGarmin]) -> None:
    session = client.GarminClient.from_tokens(TOKEN)
    fake.read_error = garminconnect.GarminConnectConnectionError("500")
    with pytest.raises(GarminUnavailable):
        session.activities_by_date(date(2026, 1, 1), date(2026, 1, 2))


def test_exercise_catalog() -> None:
    catalog = client.exercise_catalog()
    assert len(catalog) == 1527
    assert len({(e.category, e.name) for e in catalog}) == 1527
    assert all(e.category and e.name and e.display_name for e in catalog)


START, END, DAY = date(2026, 9, 21), date(2026, 10, 4), date(2026, 10, 3)
METRIC_READS = [
    ("hrv_range", (START, END), "get_hrv_data_range", {}),
    ("max_metrics_range", (START, END), "get_max_metrics_range", None),
    ("body_composition_range", (START, END), "get_body_composition", {}),
    ("training_readiness", (DAY,), "get_training_readiness", []),
    ("sleep", (DAY,), "get_sleep_data", {}),
    ("daily_summary", (DAY,), "get_user_summary", {}),
]


@pytest.mark.parametrize(("method", "args", "library", "empty"), METRIC_READS)
def test_metric_reads_pass_iso_dates(
    fake: type[FakeGarmin], method: str, args: tuple[date, ...], library: str, empty: object
) -> None:
    session = client.GarminClient.from_tokens(TOKEN)
    assert getattr(session, method)(*args) == empty  # library returned None
    fake.metric_result = {"ok": 1}
    assert getattr(session, method)(*args) == {"ok": 1}
    iso = tuple(d.isoformat() for d in args)
    assert fake.instances[0].metric_calls == [(library, iso), (library, iso)]


def test_metric_read_maps_errors(fake: type[FakeGarmin]) -> None:
    session = client.GarminClient.from_tokens(TOKEN)
    fake.metric_result = None

    def boom(*_: str) -> object:
        raise garminconnect.GarminConnectTooManyRequestsError("429")

    fake.get_sleep_data = boom  # type: ignore[method-assign,assignment]
    with pytest.raises(GarminRateLimited):
        session.sleep(DAY)


def test_workout_writes_return_ids(fake: type[FakeGarmin]) -> None:
    session = client.GarminClient.from_tokens(TOKEN)
    assert session.upload_workout({"workoutName": "Upper W1 2026-10-06"}) == 77
    session.update_workout(77, {"workoutName": "Upper W1 2026-10-06"})
    assert session.schedule_workout(77, date(2026, 10, 6)) == 88
    assert session.unschedule_workout(88) is True
    assert session.delete_workout(77) is True
    assert fake.instances[0].metric_calls == [
        ("upload_workout", ("Upper W1 2026-10-06",)),
        ("update_workout", ("77", "Upper W1 2026-10-06")),
        ("schedule_workout", ("77", "2026-10-06")),
        ("unschedule_workout", ("88",)),
        ("delete_workout", ("77",)),
    ]


def test_removing_a_missing_workout_is_not_an_error(fake: type[FakeGarmin]) -> None:
    session = client.GarminClient.from_tokens(TOKEN)
    fake.write_error = garminconnect.GarminConnectNotFoundError("API Error 404")
    assert session.unschedule_workout(88) is False
    assert session.delete_workout(77) is False
    with pytest.raises(GarminNotFound):
        session.update_workout(77, {"workoutName": "x"})


def test_workout_write_errors_map_to_domain_errors(fake: type[FakeGarmin]) -> None:
    session = client.GarminClient.from_tokens(TOKEN)
    fake.write_error = garminconnect.GarminConnectConnectionError("API Error 500")
    with pytest.raises(GarminUnavailable):
        session.delete_workout(77)
