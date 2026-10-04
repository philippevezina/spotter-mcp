"""Client wrapper tests with a fake garminconnect.Garmin. No network."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date
from typing import ClassVar

import garminconnect
import pytest

from spotter.garmin import client
from spotter.garmin.errors import GarminAuthExpired, GarminRateLimited, GarminUnavailable

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
