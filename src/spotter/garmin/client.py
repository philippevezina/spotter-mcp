"""The only module that imports garminconnect (pinned 0.3.17).

Server code only loads stored tokens. `login_with_credentials` is for the local CLI.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import garminconnect
from garminconnect import exercises as garmin_exercises

from spotter.garmin.errors import GarminAuthExpired, GarminRateLimited


@dataclass(frozen=True)
class CatalogEntry:
    display_name: str
    category: str
    name: str


def exercise_catalog() -> list[CatalogEntry]:
    """Garmin's exercise catalog bundled with the library (1,527 entries in 0.3.17)."""
    return [
        CatalogEntry(display_name=e["name"], category=e["category"], name=e["exercise"])
        for e in garmin_exercises.EXERCISES
    ]


def login_with_credentials(email: str, password: str, prompt_mfa: Callable[[], str]) -> str:
    """Interactive login. Returns the token store JSON. Never call from server code."""
    api = garminconnect.Garmin(email, password, prompt_mfa=prompt_mfa)
    try:
        api.login()
    except garminconnect.GarminConnectTooManyRequestsError as exc:
        raise GarminRateLimited(
            "Garmin returned 429 on login. Stop here and do not retry: blocks can last days."
        ) from exc
    except garminconnect.GarminConnectAuthenticationError as exc:
        raise GarminAuthExpired("login rejected") from exc
    dumped: str = api.client.dumps()
    return dumped


class GarminClient:
    """A Garmin session restored from a token store. Never uses credentials."""

    def __init__(self, api: Any) -> None:
        self._api = api

    @classmethod
    def from_tokens(cls, token_json: str) -> GarminClient:
        api = garminconnect.Garmin()
        try:
            api.login(tokenstore=token_json)
        except garminconnect.GarminConnectTooManyRequestsError as exc:
            raise GarminRateLimited("Garmin returned 429. Try again later.") from exc
        except garminconnect.GarminConnectAuthenticationError as exc:
            raise GarminAuthExpired() from exc
        return cls(api)

    def dumps(self) -> str:
        """Current token store JSON. Differs from the input after a refresh."""
        dumped: str = self._api.client.dumps()
        return dumped

    def check(self) -> None:
        """One cheap authenticated read, to prove the session works."""
        try:
            self._api.get_unit_system()
        except garminconnect.GarminConnectTooManyRequestsError as exc:
            raise GarminRateLimited("Garmin returned 429. Try again later.") from exc
        except garminconnect.GarminConnectAuthenticationError as exc:
            raise GarminAuthExpired() from exc
