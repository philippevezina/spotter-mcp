from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, select

from spotter.db.schema import garmin_tokens
from spotter.garmin import tokens
from spotter.garmin.errors import TokenKeyInvalid

TOKEN = json.dumps({"di_token": "t1", "di_refresh_token": "r1", "di_client_id": "c"})


def test_round_trip_is_encrypted(db: Engine, token_key: str) -> None:
    with db.begin() as conn:
        tokens.save_tokens(conn, TOKEN, token_key)
    with db.connect() as conn:
        stored = conn.execute(select(garmin_tokens.c.ciphertext)).scalar_one()
        assert b"r1" not in bytes(stored)
        assert tokens.load_tokens(conn, token_key) == TOKEN


def test_save_overwrites_single_row(db: Engine, token_key: str) -> None:
    newer = TOKEN.replace("t1", "t2")
    with db.begin() as conn:
        tokens.save_tokens(conn, TOKEN, token_key)
        tokens.save_tokens(conn, newer, token_key)
    with db.connect() as conn:
        assert conn.execute(select(garmin_tokens.c.id)).scalars().all() == [1]
        assert tokens.load_tokens(conn, token_key) == newer


def test_load_when_empty(db: Engine, token_key: str) -> None:
    with db.connect() as conn:
        assert tokens.load_tokens(conn, token_key) is None


def test_wrong_key(db: Engine, token_key: str) -> None:
    with db.begin() as conn:
        tokens.save_tokens(conn, TOKEN, token_key)
    with db.connect() as conn, pytest.raises(TokenKeyInvalid, match="does not decrypt"):
        tokens.load_tokens(conn, Fernet.generate_key().decode())


def test_malformed_key(db: Engine) -> None:
    with db.begin() as conn, pytest.raises(TokenKeyInvalid, match="not a valid Fernet key"):
        tokens.save_tokens(conn, TOKEN, "nope")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not json", "not valid JSON"),
        ("[1]", "JSON object"),
        (json.dumps({"di_token": "secret-value"}), "di_client_id, di_refresh_token"),
    ],
)
def test_validate_rejects(payload: str, message: str) -> None:
    with pytest.raises(ValueError, match=message) as info:
        tokens.validate_token_json(payload)
    assert "secret-value" not in str(info.value)


def test_save_if_changed(db: Engine, token_key: str) -> None:
    reordered = json.dumps(dict(reversed(list(json.loads(TOKEN).items()))))
    refreshed = TOKEN.replace("t1", "t2")
    with db.begin() as conn:
        tokens.save_tokens(conn, TOKEN, token_key)
        assert tokens.save_if_changed(conn, TOKEN, reordered, token_key) is False
        assert tokens.load_tokens(conn, token_key) == TOKEN
        assert tokens.save_if_changed(conn, TOKEN, refreshed, token_key) is True
        assert tokens.load_tokens(conn, token_key) == refreshed
