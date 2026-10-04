"""Phase 0 checks 4 and 5: stateless FastMCP on Vercel with GitHub OAuth.

Throwaway. Env vars: BASE_URL, GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET,
ALLOWED_GITHUB_LOGIN, JWT_SIGNING_KEY, STORAGE_ENCRYPTION_KEY,
DATABASE_URL_UNPOOLED (Neon direct; falls back to DATABASE_URL), GARMIN_TOKENS_JSON (check 5 only).
"""

from __future__ import annotations

import os
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from cryptography.fernet import Fernet
from fastmcp import FastMCP
from fastmcp.server.auth import AuthContext
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import AuthMiddleware
from key_value.aio.stores.postgresql import PostgreSQLStore
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper

CLAUDE_CALLBACKS = [
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
]


def asyncpg_url(url: str) -> str:
    """asyncpg rejects libpq-only params that Neon puts in its connection strings."""
    parts = urlsplit(url.replace("postgresql+psycopg://", "postgresql://"))
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "channel_binding"]
    return urlunsplit(parts._replace(query=urlencode(query)))


storage = FernetEncryptionWrapper(
    PostgreSQLStore(
        url=asyncpg_url(os.environ.get("DATABASE_URL_UNPOOLED") or os.environ["DATABASE_URL"]),
        table_name="spike_kv_store",
    ),
    fernet=Fernet(os.environ["STORAGE_ENCRYPTION_KEY"]),
)

auth = GitHubProvider(
    client_id=os.environ["GITHUB_CLIENT_ID"],
    client_secret=os.environ["GITHUB_CLIENT_SECRET"],
    base_url=os.environ["BASE_URL"],
    jwt_signing_key=os.environ["JWT_SIGNING_KEY"],
    client_storage=storage,
    allowed_client_redirect_uris=CLAUDE_CALLBACKS,
    required_scopes=["read:user"],  # identity only; the default "user" can write the profile
)

ALLOWED_LOGIN = os.environ["ALLOWED_GITHUB_LOGIN"]


def allowed(ctx: AuthContext) -> bool:
    return ctx.token is not None and ctx.token.claims.get("login") == ALLOWED_LOGIN


mcp = FastMCP("Spotter spike", auth=auth, middleware=[AuthMiddleware(auth=allowed)])


@mcp.tool
def whoami() -> dict[str, Any]:
    """Return the GitHub identity of the caller. Phase 0 spike."""
    claims = get_access_token().claims
    return {"login": claims.get("login"), "sub": claims.get("sub")}


@mcp.tool
def garmin_recent_activities(days: int = 7) -> dict[str, Any]:
    """List the athlete's Garmin activities for the last `days` days. Phase 0 spike.

    Confirms the Garmin API accepts calls from Vercel. Uses stored tokens only, never credentials.
    """
    from garminconnect import Garmin

    tokens = os.environ.get("GARMIN_TOKENS_JSON")
    if not tokens:
        return {"ok": False, "error": "GARMIN_TOKENS_JSON is not set"}

    started = time.monotonic()
    garmin = Garmin()
    try:
        garmin.login(tokenstore=tokens)
        start = (date.today() - timedelta(days=days)).isoformat()
        activities = garmin.get_activities_by_date(start, date.today().isoformat())
    except Exception as exc:  # spike: report the failure class, never payloads
        return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)[:300],
                "elapsed_ms": round((time.monotonic() - started) * 1000)}

    return {
        "ok": True,
        "elapsed_ms": round((time.monotonic() - started) * 1000),
        "tokens_refreshed": garmin.client.dumps() != tokens,
        "count": len(activities),
        "activities": [
            {
                "date": (a.get("startTimeLocal") or "")[:10],
                "type": a.get("activityType", {}).get("typeKey"),
                "duration_min": round((a.get("duration") or 0) / 60),
            }
            for a in activities
        ],
    }


app = mcp.http_app(path="/mcp", stateless_http=True)
