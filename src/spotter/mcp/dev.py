"""Local development server with no auth: `uv run fastmcp dev src/spotter/mcp/dev.py`.

Uses `.env`, so it reaches the compose Postgres. Never deploy this module.
"""

from __future__ import annotations

from spotter.config import get_settings
from spotter.mcp.deps import Deps
from spotter.mcp.server import build_server

mcp = build_server(Deps.from_settings(get_settings()))
