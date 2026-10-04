"""Allowlist and server settings. No network: building the app connects to nothing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from cryptography.fernet import Fernet
from starlette.applications import Starlette

from spotter.config import Settings
from spotter.mcp.auth import ServerConfigError, allow_only, asyncpg_url, check_settings
from spotter.mcp.server import create_app


def ctx(claims: dict[str, Any] | None) -> Any:
    return SimpleNamespace(token=None if claims is None else SimpleNamespace(claims=claims))


def test_allow_only_matches_sub() -> None:
    check = allow_only("12345")
    assert check(ctx({"sub": "12345", "login": "athlete"}))
    assert check(ctx({"sub": 12345}))  # numeric claim
    assert not check(ctx({"sub": "999", "login": "athlete"}))
    assert not check(ctx({"login": "12345"}))  # login never counts
    assert not check(ctx(None))


def settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/db",
        "database_url_unpooled": "postgresql://u:p@localhost/db?sslmode=require&channel_binding=require",
        "base_url": "https://example.test",
        "github_client_id": "id",
        "github_client_secret": "secret",
        "allowed_github_user_id": "12345",
        "jwt_signing_key": "k" * 48,
        "storage_encryption_key": Fernet.generate_key().decode(),
        **overrides,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def test_missing_settings_named_without_values() -> None:
    with pytest.raises(ServerConfigError) as exc:
        check_settings(settings(github_client_secret=None, allowed_github_user_id=None))
    assert str(exc.value) == (
        "Missing environment variables: GITHUB_CLIENT_SECRET, ALLOWED_GITHUB_USER_ID"
    )


def test_create_app_is_plain_asgi() -> None:
    assert isinstance(create_app(settings()), Starlette)


def test_create_app_refuses_missing_settings() -> None:
    with pytest.raises(ServerConfigError, match="JWT_SIGNING_KEY"):
        create_app(settings(jwt_signing_key=None))


def test_asyncpg_url_strips_libpq_params() -> None:
    url = asyncpg_url("postgresql+psycopg://u:p@h/db?sslmode=require&channel_binding=require")
    assert url == "postgresql://u:p@h/db?sslmode=require"
