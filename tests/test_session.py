from __future__ import annotations

from typing import ClassVar

import garminconnect
import pytest
from sqlalchemy import Engine

from spotter.garmin import tokens
from spotter.garmin.errors import GarminTokensMissing
from spotter.garmin.session import garmin_session
from tests.test_garmin_client import REFRESHED, TOKEN, FakeGarmin


@pytest.fixture
def fake(env: pytest.MonkeyPatch) -> type[FakeGarmin]:
    class Fake(FakeGarmin):
        instances: ClassVar[list[FakeGarmin]] = []

    env.setattr(garminconnect, "Garmin", Fake)
    return Fake


def test_refresh_saved_even_when_block_fails(
    fake: type[FakeGarmin], db: Engine, token_key: str
) -> None:
    with db.begin() as conn:
        tokens.save_tokens(conn, TOKEN, token_key)
    fake.refresh_on_login = True
    with pytest.raises(RuntimeError), garmin_session(db) as session:
        raise RuntimeError("sync failed")
    assert session.refreshed
    with db.connect() as conn:
        assert tokens.load_tokens(conn, token_key) == REFRESHED


def test_missing_tokens(fake: type[FakeGarmin], db: Engine) -> None:
    with pytest.raises(GarminTokensMissing), garmin_session(db):
        pass
    assert fake.instances == []
