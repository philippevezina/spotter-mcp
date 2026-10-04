"""Garmin token store, Fernet-encrypted in the `garmin_tokens` table.

Never log or print token content. Callers own the transaction.
"""

from __future__ import annotations

import json

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert

from spotter.config import get_settings
from spotter.db.schema import garmin_tokens
from spotter.garmin.errors import TokenKeyInvalid

# Keys of the token store JSON from `client.dumps()` (decisions.md, Phase 0 check 1).
TOKEN_KEYS = frozenset({"di_token", "di_refresh_token", "di_client_id"})


def _fernet(key: str | None = None) -> Fernet:
    if key is None:
        secret = get_settings().garmin_token_key
        key = secret.get_secret_value() if secret else None
    if not key:
        raise TokenKeyInvalid("GARMIN_TOKEN_KEY is not set.")
    try:
        return Fernet(key)
    except ValueError as exc:
        raise TokenKeyInvalid("GARMIN_TOKEN_KEY is not a valid Fernet key.") from exc


def validate_token_json(token_json: str) -> dict[str, object]:
    """Parse and check required keys. Errors name keys only, never values."""
    try:
        data = json.loads(token_json)
    except json.JSONDecodeError as exc:
        raise ValueError("Token store is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise ValueError("Token store must be a JSON object.")
    missing = sorted(TOKEN_KEYS - data.keys())
    if missing:
        raise ValueError(f"Token store is missing keys: {', '.join(missing)}")
    return data


def save_tokens(conn: Connection, token_json: str, key: str | None = None) -> None:
    validate_token_json(token_json)
    ciphertext = _fernet(key).encrypt(token_json.encode())
    stmt = insert(garmin_tokens).values(id=1, ciphertext=ciphertext)
    conn.execute(
        stmt.on_conflict_do_update(
            index_elements=[garmin_tokens.c.id],
            set_={"ciphertext": stmt.excluded.ciphertext, "updated_at": func.now()},
        )
    )


def load_tokens(conn: Connection, key: str | None = None) -> str | None:
    ciphertext = conn.execute(
        select(garmin_tokens.c.ciphertext).where(garmin_tokens.c.id == 1)
    ).scalar_one_or_none()
    if ciphertext is None:
        return None
    try:
        return _fernet(key).decrypt(bytes(ciphertext)).decode()
    except InvalidToken as exc:
        raise TokenKeyInvalid("GARMIN_TOKEN_KEY does not decrypt the stored tokens.") from exc


def save_if_changed(conn: Connection, before: str, after: str, key: str | None = None) -> bool:
    """Persist a refreshed token store. Returns True when it changed and was saved."""
    if json.loads(after) == json.loads(before):
        return False
    save_tokens(conn, after, key)
    return True
