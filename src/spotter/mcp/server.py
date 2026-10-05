"""FastMCP server: tool registration, auth and the ASGI app factory (SPEC sections 11 and 12).

`build_server` takes its dependencies so tests run it in process with no auth.
`create_app` wires production settings. The module-level app lives in
`spotter.mcp.asgi`, so importing this module never needs secrets.
"""

from __future__ import annotations

from pathlib import Path

from fastmcp import FastMCP
from fastmcp.server.auth import AuthProvider
from fastmcp.server.middleware import AuthMiddleware, Middleware
from starlette.applications import Starlette

from spotter.config import Settings, get_settings
from spotter.mcp.auth import allow_only, build_auth
from spotter.mcp.deps import Deps
from spotter.mcp.tools import admin, calculation, context, garmin_write, write

INSTRUCTIONS = (Path(__file__).parent / "instructions.md").read_text()


def build_server(
    deps: Deps,
    auth: AuthProvider | None = None,
    middleware: list[Middleware] | None = None,
) -> FastMCP:
    mcp = FastMCP("Spotter", instructions=INSTRUCTIONS, auth=auth, middleware=middleware)
    context.register(mcp, deps)
    calculation.register(mcp, deps)
    write.register(mcp, deps)
    garmin_write.register(mcp, deps)
    admin.register(mcp, deps)
    return mcp


def create_app(settings: Settings | None = None) -> Starlette:
    settings = settings or get_settings()
    auth = build_auth(settings)  # raises ServerConfigError when a variable is missing
    assert settings.allowed_github_user_id
    mcp = build_server(
        Deps.from_settings(settings),
        auth=auth,
        middleware=[AuthMiddleware(auth=allow_only(settings.allowed_github_user_id))],
    )
    return mcp.http_app(path="/mcp", stateless_http=True)
