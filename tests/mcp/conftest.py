"""In-process MCP server against the test database, a fake Garmin session and a fixed clock."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastmcp import Client, FastMCP
from sqlalchemy import Engine

from spotter.db.seed import seed_exercises
from spotter.garmin.client import CatalogEntry
from spotter.garmin.session import GarminSession
from spotter.mcp.deps import Deps
from spotter.mcp.server import build_server
from tests.test_sync import BENCH, NOW, FakeClient, history


@dataclass
class FakeGarmin:
    """Stands in for `garmin_session`: counts opens, can fail on open."""

    client: FakeClient = field(default_factory=history)
    opens: int = 0
    error: Exception | None = None

    @contextmanager
    def __call__(self, engine: Engine) -> Iterator[GarminSession]:
        self.opens += 1
        if self.error is not None:
            raise self.error
        yield GarminSession(self.client)  # type: ignore[arg-type]


@pytest.fixture
def garmin() -> FakeGarmin:
    return FakeGarmin()


@pytest.fixture
def catalog(db: Engine) -> Engine:
    with db.begin() as conn:
        seed_exercises(conn, [CatalogEntry("Barbell Bench Press", *BENCH)])
    return db


@pytest.fixture
def mcp(catalog: Engine, garmin: FakeGarmin) -> FastMCP:
    return build_server(Deps(catalog, catalog, garmin=garmin, now=lambda: NOW))


Call = Callable[..., Any]


@pytest.fixture
def call(mcp: FastMCP) -> Call:
    """Call a tool in process and return its structured result. Tool errors raise."""

    def _call(name: str, **args: Any) -> Any:
        async def go() -> Any:
            async with Client(mcp) as client:
                return (await client.call_tool(name, args)).structured_content

        return asyncio.run(go())

    return _call
