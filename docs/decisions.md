# Decisions

One entry per decision. Newest at the bottom. Format: date, decision, why, status.

## 2026-10-04: Remote MCP server, no local server

Why: must work from the Claude mobile app, which only uses remote MCP connectors.
Status: decided.

## 2026-10-04: Run only on demand, no background jobs

Why: plans are made and adjusted when the athlete opens a conversation. Sync happens on the first tool call.
Status: decided.

## 2026-10-04: Vercel Hobby + Neon Postgres

Why: free, HTTPS on a `*.vercel.app` domain, nothing to maintain. Vercel has no persistent disk, so all state is in Postgres.
Fallback: Oracle Always Free VM with Docker and Kamal, same code, same Neon database.
Status: decided. Phase 0 checks 4 and 5 passed on `https://spotter-mcp.vercel.app` (region `iad1`, Neon `us-east-2`). No Oracle fallback needed.

## 2026-10-04: python-garminconnect 0.3.17, pinned

Why: supports strength workouts with target weights, scheduling, exercise sets read and write, and the readiness metrics needed. Python, so the server is Python.
Status: decided.

## 2026-10-04: Store kg, display lb

Why: athlete thinks in lb at the gym; Garmin stores metric.
Status: decided. Round trip confirmed in Phase 0 check 3. Encoding details in "Workout step weights" below.

## 2026-10-04: GitHub OAuth via FastMCP, single allowed login

Why: avoids running an authorization server. Allowlist restricts access to one user.
Status: decided. Phase 0 check 4 passed. Request only the `read:user` scope (the provider default `user` can write the profile). Use a GitHub OAuth App, not a GitHub App: only identity is needed, and FastMCP documents the OAuth App path.

## 2026-10-04: Project name is Spotter

Why: "Garmin Coach" is a Garmin product name. Third-party marks (Garmin, Claude) stay out of the project name. Describe compatibility in text only, e.g. "works with Garmin Connect".
Names: repo `spotter-mcp`, Python package `spotter`, CLI `spotter`, Vercel project `spotter-mcp`, Claude connector name "Spotter".
Status: decided. Check GitHub repo name availability before creating it.

## Phase 0 results

| Check | Result | Notes |
|---|---|---|
| 1. Local login (MFA?) | Pass (2026-10-04) | No MFA prompt. The two mobile login methods returned 429; a later fallback method succeeded. Tokens: `di_token`, `di_refresh_token`, `di_client_id`. |
| 2. Read strength sets, lb correct | Pass (2026-10-04) | Account had no strength history, so this used a session logged from the check 3 workout. Bench logged at 190 lb came back as 86187 g = 190.0 lb. Fixture in `tests/fixtures/garmin/`. |
| 3. Push workout, weight shown on watch in lb | Pass (2026-10-04), after a fix | The helper's gram encoding did not display on the watch. Kg with 2 decimals displays 190 / 225 / 205 lb correctly. See "Workout step weights" below. |
| 4. FastMCP + GitHub auth on Vercel, used from phone | Pass (2026-10-04) | Connector added on claude.ai web; `whoami` returned login and id from the phone. A wrong `ALLOWED_GITHUB_LOGIN` was denied. Connection survived a redeploy without reconnecting. After 15+ min idle, a new instance (new `instance_id`) served `whoami` with no reconnect. |
| 5. Garmin call from Vercel IPs | Pass (2026-10-04) | `garmin_recent_activities` returned the athlete's run from the phone. No 403 or 429 (garminconnect issue #444 did not reproduce). Tokens loaded from an env var, no credentials. |

Phase 0 exit criteria met on 2026-10-04: all five checks pass. Phase 1 may start.

## 2026-10-04: fastmcp 4.x, pinned

Why: SPEC said 2.x, but 4.0.10 is current and carries OAuthProxy token fixes. 2.x gets no further fixes.
Changes: since 3.0, `stateless_http` goes to `http_app()`, not the `FastMCP()` constructor.
Status: decided. Spike uses `fastmcp==4.0.10`.

## 2026-10-04: Garmin token store API (garminconnect 0.3.17)

- Dump with `garmin.client.dumps()`, load with `Garmin().login(tokenstore=<inline JSON>)`. Base64 is not accepted. There is no `garmin.garth`.
- Inline JSON is never auto-saved. To catch a refresh, compare `client.dumps()` before and after the calls, then re-encrypt and save.
- Phase 0 check 5: the token from the Mac was still valid on Vercel, so no refresh happened (`tokens_refreshed: false`). Whether a refresh rotates `di_refresh_token` is still unknown. Phase 1 saves the token whenever `dumps()` changes, which covers both cases.
- Login from the Mac only, once. On 429, stop. On the first run, the first two of five login methods returned 429 and a later one succeeded.
Status: decided.

## 2026-10-04: Workout step weights are kg with 2 decimals

Finding: `create_strength_exercise_step(weight_kg=...)` sends `weightValue` in grams (kg × 1000). Garmin accepts it, but the watch shows no weight. The Connect app stores the same step as kg with 2 decimals (185 lb → 83.91) under the same unit tag `{"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}`. Sent that way, the watch shows the correct lb values.
Decision: `spotter.garmin.workouts` writes `weightValue` itself as kg rounded to 0.01, from the exact conversion (round once, not 3 decimals then 2). Do not rely on the helper's weight handling.
Read side differs: performed sets (`get_activity_exercise_sets`) return `weight` in grams (190 lb logged → 86187). The watch does its own lb → g conversion. Both round to the same lb at 0.5 lb.
Workout read-back (`get_workout_by_id`) can mix encodings if a workout was ever pushed in grams. Spotter only writes kg.
Status: decided. SPEC section 6 updated.

## 2026-10-04: Re-push uses update_workout

Finding: `update_workout(workout_id, json)` replaced every step, and the workout id was still on the October 2026 calendar afterwards. Schedule id itself not re-checked.
Decision: `push_session` updates the existing Garmin workout when `garmin_workout_id` is set, and only uploads plus schedules when it is not.
Untested: whether `unschedule_workout` or `delete_workout` affects an activity already linked to that workout. Phase 5 must test this before `unschedule_session` touches a completed session.
Status: decided.

## 2026-10-04: Activity to workout link, and set-level fields

- An activity started from a pushed workout carries `workoutId` (list summary) and `metadataDTO.associatedWorkoutId` (full activity). Sync links by workout id first, then by date (SPEC section 9). Resolves SPEC open item 3.
- Each set row has `wktStepIndex`, which points at the workout step it came from.
- The workout `description` is accepted and stored. Resolves SPEC open item 2.
- A set logged with 0 reps still comes back as `setType: ACTIVE` with a weight. Stats must ignore 0-rep sets.
- `exercises` on a set is a list of candidates with a `probability`. Map using the highest-probability entry.
Status: decided.

## 2026-10-04: OAuth client storage uses py-key-value-aio PostgreSQLStore

Finding: py-key-value-aio 0.4.6 ships a `PostgreSQLStore` (asyncpg). A custom adapter is not needed.
Tradeoffs: adds asyncpg next to psycopg. It manages its own table (`value jsonb`, `ttl`, `created_at`, `expires_at`), which differs from SPEC's `kv_store`. The library marks it as unstable.
Status: decided. Works on Vercel: client registration, login and tokens survived a redeploy. Use the direct (unpooled) Neon URL. Phase 3 lets the store own its table, and SPEC section 7 drops `kv_store`.

## 2026-10-04: Vercel deployment shape

- Vercel root directory points at the app folder. `api/index.py` exposes the ASGI `app`, and `vercel.json` rewrites every path to `/api/index`, so `/.well-known/*`, `/authorize`, `/token`, `/register` and `/mcp` all reach the app.
- `mcp.http_app(path="/mcp", stateless_http=True)` works on Vercel's Python runtime. Authenticated MCP calls succeed, so the app's lifespan startup runs.
- Vercel deploys production from the repo's production branch. Keep `BASE_URL` and the GitHub OAuth callback on the production domain.
- Server `date.today()` is UTC on Vercel. Phase 3 must compute dates in America/Toronto.
Status: decided.

## 2026-10-04: Allowlist on GitHub user id, not login (proposed)

Why: `login` changes if the GitHub account is renamed, which would lock the athlete out. The numeric `sub` claim is permanent. `whoami` returns both.
Status: proposed for Phase 3. Spike keeps `ALLOWED_GITHUB_LOGIN`.

## 2026-10-04: One Garmin token copy; import the spike token in Phase 1 (proposed)

Why: an env var is read-only at runtime, so a server copy of the token can never save a refresh. If Garmin rotates refresh tokens, two copies (Mac and Vercel) break each other, and recovery needs a new login with 429 risk.
Done: `GARMIN_TOKENS_JSON` removed from Vercel after Phase 0. The only live copy is `spikes/.tokens/garmin_tokens.json` on the Mac.
Proposed for Phase 1: add `spotter import-tokens <path>` to load that file into `garmin_tokens` (encrypted), and use it instead of a fresh `bootstrap-login`. Keep `bootstrap-login` for when the token dies.
Status: proposed.
