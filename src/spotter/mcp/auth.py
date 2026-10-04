"""GitHub OAuth via FastMCP, restricted to one GitHub user id (SPEC section 12).

The allowlist checks the numeric `sub` claim, not `login`, which changes on a
rename (decisions.md). It runs as middleware on every request, never per tool.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth.providers.github import GitHubProvider
from key_value.aio.stores.postgresql import PostgreSQLStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

from spotter.config import Settings

CLAUDE_CALLBACKS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]
OAUTH_TABLE = "oauth_store"  # owned by PostgreSQLStore, not Alembic (decisions.md)
TOKEN_CACHE_S = 300  # skip a GitHub API call on every request

REQUIRED = (
    "base_url",
    "github_client_id",
    "github_client_secret",
    "allowed_github_user_id",
    "jwt_signing_key",
    "storage_encryption_key",
)


class ServerConfigError(RuntimeError):
    """A required server setting is missing. Names variables only, never values."""


def check_settings(settings: Settings) -> None:
    missing = [name.upper() for name in REQUIRED if not getattr(settings, name)]
    if missing:
        raise ServerConfigError(f"Missing environment variables: {', '.join(missing)}")


def asyncpg_url(url: str) -> str:
    """asyncpg rejects libpq-only params that Neon puts in its connection strings."""
    parts = urlsplit(url.replace("postgresql+psycopg://", "postgresql://"))
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return urlunsplit(parts._replace(query=urlencode(query)))


def _secret(settings: Settings, name: str) -> str:
    value = getattr(settings, name)
    assert value is not None  # check_settings ran
    return str(value.get_secret_value())


def build_auth(settings: Settings) -> GitHubProvider:
    check_settings(settings)
    storage = FernetEncryptionWrapper(
        PostgreSQLStore(
            url=asyncpg_url(settings.database_url_unpooled or settings.database_url),
            table_name=OAUTH_TABLE,
        ),
        fernet=Fernet(_secret(settings, "storage_encryption_key")),
    )
    assert settings.github_client_id and settings.base_url
    return GitHubProvider(
        client_id=settings.github_client_id,
        client_secret=_secret(settings, "github_client_secret"),
        base_url=settings.base_url,
        jwt_signing_key=_secret(settings, "jwt_signing_key"),
        client_storage=storage,
        allowed_client_redirect_uris=CLAUDE_CALLBACKS,
        required_scopes=["read:user"],  # identity only; the default "user" can write the profile
        cache_ttl_seconds=TOKEN_CACHE_S,
    )


def allow_only(user_id: str) -> Callable[[AuthContext], bool]:
    """Auth check for `AuthMiddleware`: the caller's GitHub `sub` must equal `user_id`."""

    def check(ctx: AuthContext) -> bool:
        return ctx.token is not None and str(ctx.token.claims.get("sub")) == user_id

    return check
