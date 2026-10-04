# Spotter

Spotter (`spotter-mcp`) is a remote MCP server that lets Claude plan strength training and push workouts to Garmin Connect.
Single user. Deployed on Vercel (Python), data in Neon Postgres.

Read before any work:

- `docs/SPEC.md`: architecture, schema, tools, phases. Source of truth.
- `docs/coaching-rules.md`: engine rules (Part A) and server instructions (Part B).
- `docs/decisions.md`: decisions made so far, including Phase 0 results.

## Working agreement

- Work one phase at a time (SPEC section 15). Start in plan mode. Do not start a phase before the previous phase's exit criteria pass.
- If the spec is wrong or unclear, stop and ask. Record any decision in `docs/decisions.md`.
- Keep PRs to one phase or less.

## Commands

```bash
uv sync                                  # install
docker compose up -d db                  # local Postgres
uv run alembic upgrade head              # migrate
uv run pytest                            # tests
uv run pytest tests/engine --cov=spotter.engine --cov-branch --cov-fail-under=100
uv run ruff check . && uv run ruff format --check .
uv run mypy src/spotter/engine
uv run spotter --help                    # CLI: bootstrap-login, import-tokens, garmin-check, seed-exercises,
                                         #      sync, backfill --since, rebuild-stats, strength-log
uv run --env-file .env.local spotter ... # same, against production (Neon)
uv run fastmcp dev src/spotter/mcp/server.py   # local MCP inspector
```

## Hard rules

- Only `src/spotter/garmin/client.py` imports `garminconnect`. Version is pinned. Do not upgrade it without asking.
- Never call Garmin login from server code. The server only loads and refreshes stored tokens.
- Never hit the real Garmin API in tests. Use fixtures in `tests/fixtures/garmin/`. Anonymize fixtures before committing.
- Never log tokens, credentials, or raw Garmin payloads.
- `spotter.engine` is pure: no I/O, no database, no network. Every rule in coaching-rules.md Part A has a test.
- All thresholds live in `spotter.engine.rules`. No magic numbers in logic.
- Weights: store kg, show lb. Only `spotter.units` converts. Tool inputs and outputs are lb.
- Tools return compact JSON, never raw Garmin JSON.
- Garmin write tools must be idempotent and support `dry_run`.
- Never put third-party trademarks (Garmin, Claude) in project, package, module, CLI or connector names. Mention them only in descriptions, e.g. "works with Garmin Connect".
- No Vercel-specific code outside `api/index.py`. The app must also run as a plain ASGI app in Docker.

## Conventions

- Python 3.12, type hints everywhere, `from __future__ import annotations`.
- SQLAlchemy 2.0 Core (not ORM), psycopg 3. One Alembic migration per schema change.
- Dates in the athlete's local time zone (America/Toronto) as `date`; timestamps as UTC `timestamptz`.
- Tool descriptions state their own preconditions (e.g. "Call only after the athlete confirms").
- Commit messages: imperative, short subject, body explains why.
