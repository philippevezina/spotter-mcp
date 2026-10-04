# Phase 0 spikes

Throwaway scripts that answer the go / no-go questions in SPEC section 15.
Record every result in `docs/decisions.md`.

Tokens go to `spikes/.tokens/` and raw Garmin payloads to `spikes/out/`. Both are gitignored.
Scripts never print tokens or raw payloads.

## Checks 1 to 3: local, run in order

```bash
# 1. Log in once. Interactive (password + MFA if asked). On 429: stop, do not retry.
uv run spikes/bootstrap_login.py

# 2. Read the newest strength activity. Compare the lb column with what you logged.
uv run spikes/read_strength.py            # or --activity-id N, --days 365
uv run spikes/anonymize_fixture.py <activity_id>   # then review the printed strings

# 3. Push a test workout for tomorrow.
uv run spikes/push_test_workout.py        # dry run: prints steps, no Garmin call
uv run spikes/push_test_workout.py --push
#    Sync the watch. Expect: bench 3x5 @ 185 lb, squat 1x3 @ 225 lb, 2x5 @ 205 lb.
uv run spikes/push_test_workout.py --update    # bench -> 190 lb, schedule must survive
#    Sync again, expect 190 lb. Log bench at 185 lb in a session, then re-run read_strength.py.
uv run spikes/push_test_workout.py --cleanup
```

## Checks 4 and 5: `vercel_hello/`

History only. Since Phase 3b the Vercel project deploys the real server from the repo root (`api/index.py`), and `spotter-mcp.vercel.app` no longer serves this spike.

1. Neon: project `spotter-mcp`, database `spotter`, branch `production`. `neon config init` writes both
   connection strings to `.env.local`. The server creates its own `spike_kv_store` table on first use.
2. GitHub OAuth App: homepage `https://spotter-mcp.vercel.app`,
   callback `https://spotter-mcp.vercel.app/auth/callback`.
3. Vercel project `spotter-mcp`, root directory `spikes/vercel_hello`. Env vars:

   | Var | Value |
   |---|---|
   | `BASE_URL` | `https://spotter-mcp.vercel.app` |
   | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | from the OAuth App |
   | `ALLOWED_GITHUB_LOGIN` | your GitHub login |
   | `JWT_SIGNING_KEY` | `python3 -c "import secrets;print(secrets.token_urlsafe(48))"` |
   | `STORAGE_ENCRYPTION_KEY` | `uv run --with cryptography python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"` |
   | `DATABASE_URL_UNPOOLED` | Neon direct connection string (from `.env.local`) |
   | `GARMIN_TOKENS_JSON` | contents of `spikes/.tokens/garmin_tokens.json` (check 5) |

4. Deploy: `vercel --prod` from `spikes/vercel_hello`.
5. Smoke test:
   ```bash
   curl -s https://spotter-mcp.vercel.app/.well-known/oauth-protected-resource/mcp
   curl -si -X POST https://spotter-mcp.vercel.app/mcp | grep -i www-authenticate
   ```
6. claude.ai (web) > Settings > Connectors > Add custom connector, URL `https://spotter-mcp.vercel.app/mcp`.
   Then from the phone: "call whoami", and "call garmin_recent_activities".
7. Persistence: call again after a redeploy and after 15+ minutes idle.
   Allowlist: set `ALLOWED_GITHUB_LOGIN` to a wrong value, redeploy, confirm denial, restore.
