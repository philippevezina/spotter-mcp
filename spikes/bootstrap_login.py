# /// script
# requires-python = ">=3.12"
# dependencies = ["garminconnect[workout]==0.3.17"]
# ///
"""Phase 0 check 1: log in once locally and save the token store.

Run interactively:  uv run spikes/bootstrap_login.py
On 429, stop. Do not retry: Garmin login blocks can last days.
"""

from __future__ import annotations

import getpass
import json

from _garmin import TOKEN_FILE, save_tokens
from garminconnect import Garmin, GarminConnectTooManyRequestsError

mfa_prompted = False


def prompt_mfa() -> str:
    global mfa_prompted
    mfa_prompted = True
    return input("MFA code: ").strip()


def main() -> None:
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password: ")
    garmin = Garmin(email, password, prompt_mfa=prompt_mfa)
    try:
        garmin.login()
    except GarminConnectTooManyRequestsError as exc:
        raise SystemExit(f"429 from Garmin. Stop here, do not retry. ({exc})") from None

    dumped = garmin.client.dumps()
    save_tokens(dumped)
    print(f"MFA prompted: {mfa_prompted}")
    print(f"Token keys: {sorted(json.loads(dumped))}")
    print(f"Saved to {TOKEN_FILE} (mode 600)")


if __name__ == "__main__":
    main()
