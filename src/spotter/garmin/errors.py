"""Domain errors for the Garmin adapter. Callers catch these, never garminconnect's."""

from __future__ import annotations

RELOGIN_HINT = "Run `spotter bootstrap-login` locally."


class GarminError(Exception):
    """Base class for Garmin adapter errors."""


class GarminAuthExpired(GarminError):
    def __init__(self, detail: str = "") -> None:
        msg = f"Garmin session expired. {RELOGIN_HINT}"
        super().__init__(f"{msg} ({detail})" if detail else msg)


class GarminRateLimited(GarminError):
    """Garmin returned 429. Stop; do not retry in a loop."""


class GarminTokensMissing(GarminError):
    def __init__(self) -> None:
        super().__init__(f"No Garmin tokens stored. {RELOGIN_HINT}")


class TokenKeyInvalid(GarminError):
    """GARMIN_TOKEN_KEY is missing, malformed, or does not decrypt the stored tokens."""
