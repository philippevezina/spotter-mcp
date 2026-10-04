"""Production ASGI app. Serve with `uvicorn spotter.mcp.asgi:app`, or via api/index.py on Vercel."""

from __future__ import annotations

from spotter.mcp.server import create_app

app = create_app()
