"""Shared helpers for Phase 0 spikes. Throwaway code.

Tokens live in spikes/.tokens/garmin_tokens.json (gitignored, mode 600).
Raw Garmin payloads go to spikes/out/ (gitignored). Never print either.
"""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

SPIKES = Path(__file__).resolve().parent
TOKEN_FILE = SPIKES / ".tokens" / "garmin_tokens.json"
OUT_DIR = SPIKES / "out"

KG_PER_LB = Decimal("0.45359237")  # exact by definition


def lb_to_kg(lb: float, increment_lb: float = 5.0, places: int = 3) -> float:
    """Round to the achievable lb increment first, then convert (SPEC section 6)."""
    inc = Decimal(str(increment_lb))
    rounded = (Decimal(str(lb)) / inc).quantize(Decimal(1), ROUND_HALF_UP) * inc
    return float((rounded * KG_PER_LB).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP))


def garmin_weight(lb: float, increment_lb: float = 5.0) -> float:
    """Workout step weightValue as the Connect app writes it: kg, 2 decimals.

    Phase 0 finding: the library helper sends grams (kg * 1000), which the watch
    does not display. The app stores e.g. 185 lb as 83.91 under the same unit tag.
    """
    return lb_to_kg(lb, increment_lb, places=2)


def kg_to_lb(kg: float) -> float:
    """Convert and round to 0.5 lb for display."""
    lb = Decimal(str(kg)) / KG_PER_LB
    return float((lb * 2).quantize(Decimal(1), ROUND_HALF_UP) / 2)


def save_tokens(dumped: str) -> None:
    TOKEN_FILE.parent.mkdir(mode=0o700, exist_ok=True)
    TOKEN_FILE.write_text(dumped)
    os.chmod(TOKEN_FILE, 0o600)


def load_client() -> Any:
    """Log in from stored tokens only. Never uses credentials."""
    from garminconnect import Garmin

    if not TOKEN_FILE.exists():
        raise SystemExit("No tokens. Run: uv run spikes/bootstrap_login.py")
    before = TOKEN_FILE.read_text()
    garmin = Garmin()
    garmin.login(tokenstore=before)
    _persist_if_changed(garmin, before)
    return garmin


def close_client(garmin: Any) -> None:
    """Call after the last request: save refreshed tokens if they changed."""
    _persist_if_changed(garmin, TOKEN_FILE.read_text())


def _persist_if_changed(garmin: Any, before: str) -> None:
    after = garmin.client.dumps()
    if json.loads(after) != json.loads(before):
        old, new = json.loads(before), json.loads(after)
        rotated = old.get("di_refresh_token") != new.get("di_refresh_token")
        save_tokens(after)
        print(f"[tokens] refreshed and saved (refresh token rotated: {rotated})")


def write_out(name: str, payload: Any) -> Path:
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / name
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str))
    return path
