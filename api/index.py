"""Vercel entry point: exposes the ASGI app. The only Vercel-specific file."""

from __future__ import annotations

import sys
from pathlib import Path

# Fallback in case the runtime does not install the project package from uv.lock.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from spotter.mcp.asgi import app  # noqa: E402

__all__ = ["app"]
