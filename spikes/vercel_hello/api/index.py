"""Vercel entry point: exposes the ASGI app. Only Vercel-specific file."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from server import app  # noqa: E402

__all__ = ["app"]
