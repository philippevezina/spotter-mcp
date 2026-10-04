"""A Garmin session backed by the stored token. Saves a refreshed token on exit.

Keep one live token copy (decisions.md): every caller goes through here so a
refresh is always written back, even when the work inside the block fails.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from sqlalchemy import Engine

from spotter.garmin import tokens
from spotter.garmin.client import GarminClient
from spotter.garmin.errors import GarminTokensMissing


@dataclass
class GarminSession:
    client: GarminClient
    refreshed: bool = False


@contextmanager
def garmin_session(engine: Engine) -> Iterator[GarminSession]:
    with engine.connect() as conn:
        before = tokens.load_tokens(conn)
    if before is None:
        raise GarminTokensMissing()
    session = GarminSession(GarminClient.from_tokens(before))
    try:
        yield session
    finally:
        with engine.begin() as conn:
            session.refreshed = tokens.save_if_changed(conn, before, session.client.dumps())
