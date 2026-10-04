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

## 2026-10-04: Allowlist on GitHub user id, not login

Why: `login` changes if the GitHub account is renamed, which would lock the athlete out. The numeric `sub` claim is permanent. `whoami` returns both.
Status: decided for Phase 3. The server checks `sub` against `ALLOWED_GITHUB_USER_ID`. `ALLOWED_GITHUB_LOGIN` is removed from the server config. The spike keeps it.

## 2026-10-04: One Garmin token copy; import the spike token in Phase 1

Why: an env var is read-only at runtime, so a server copy of the token can never save a refresh. If Garmin rotates refresh tokens, two copies (Mac and Vercel) break each other, and recovery needs a new login with 429 risk.
Done: `GARMIN_TOKENS_JSON` removed from Vercel after Phase 0. The only live copy is `spikes/.tokens/garmin_tokens.json` on the Mac.
Phase 1: `spotter import-tokens <path>` loads that file into `garmin_tokens` (encrypted) instead of a fresh `bootstrap-login`. `spotter garmin-check` then proves the Neon copy works. After that passes, the Mac file is deleted. `bootstrap-login` stays for when the token dies, and is tested with a fake client only.
Status: decided (Phase 1). SPEC Phase 1 exit criterion reworded to match.

## 2026-10-04: Local Postgres via OrbStack

Why: SPEC and CLAUDE.md use `docker compose up -d db`, and Docker was not installed. OrbStack provides the Docker CLI and keeps the Docker fallback path (Oracle VM) tested.
Tests create a fresh `spotter_test` database on that server each run (`TEST_DATABASE_URL` overrides). They fail, not skip, when it is down.
Status: decided.

## 2026-10-04: No kv_store table

Why: py-key-value's `PostgreSQLStore` owns its OAuth client table (see "OAuth client storage" above). The Phase 1 migration leaves `kv_store` out, and SPEC sections 7 and 12 are updated.
Status: decided.

## 2026-10-04: `.env` is local, `.env.local` is production

`spotter.config` reads `.env` only, which points at the compose Postgres. Commands against Neon pass the file explicitly: `uv run --env-file .env.local spotter <command>` (and the same for `alembic`). A bare command only reaches Neon if `DATABASE_URL` is exported in the shell, so do not export it.
Migrations use `DATABASE_URL_UNPOOLED` when set, since Neon's pooler does not suit DDL sessions.
Status: decided.

## Phase 1 results

Exit criteria met on 2026-10-04: `uv run pytest` green on the compose Postgres; migration applied to Neon; `exercises` has 1,527 rows; the Phase 0 token was imported into `garmin_tokens` and `spotter garmin-check` passed. The Mac copy (`spikes/.tokens/garmin_tokens.json`) is deleted, so Neon holds the only live token. The Phase 0 spike scripts no longer have a token to read. Phase 2 may start.

## 2026-10-04: Phase 2 scope: daily metrics move to Phase 3

Why: Phase 2's exit criteria cover strength history only, and the daily-metrics endpoints had no fixtures. Readiness (Phase 3) is the only consumer. Phase 2 syncs activity summaries of every type plus strength sets.
Status: decided. SPEC sections 9 and 15 updated.

## 2026-10-04: e1RM stays null until curation

Rule A.2 applies as written: `best_e1rm_kg` only for `e1rm_eligible` exercises, and every seeded exercise is `false` until Phase 4 curation. `spotter rebuild-stats [--exercise-id N]` (and `stats.rebuild_all`) recomputes stats after curation or remapping. Rebuilds upsert on `(activity_id, exercise_id)`, so Phase 5 links survive.
Status: decided.

## 2026-10-04: Sync mechanics

- Lock: `pg_try_advisory_lock` held on a dedicated connection for the whole run. The CLI opens it on `DATABASE_URL_UNPOOLED`: Neon's pooler runs in transaction mode, where session locks are unsafe. A dropped connection releases the lock, so a killed function cannot leave it held.
- Window: `--since`, else `cursor.synced_through` minus 2 days, else the last 28 days. It ends today in the athlete's time zone.
- One `get_activities_by_date` call lists the window (the library paginates). Non-strength activities are upserted from it, with no extra call.
- Strength sets are fetched newest first when the activity is new, dated within the last 2 days (to catch edits made later in the app), or `--full`. The activity row, a replace-all of its sets, and its stats commit together, so a strength activity row always has its sets.
- The 60 s budget is checked before each set fetch. When it runs out, the run returns `partial: true`. The cursor and `last_synced_at` move only on a complete run, and a partial run resumes because stored activities are skipped.
- Less than 10 minutes since the last complete run: skipped unless `force`. `backfill` always forces and has no budget.
- Activities deleted in Garmin are not detected. Accepted for v1.
Status: decided.

## 2026-10-04: Mapping details

- `local_date` is `startTimeGMT` converted to the athlete's time zone (athlete row, else America/Toronto), not Garmin's `startTimeLocal`, so it agrees with scheduled dates while travelling.
- Set `startTime` is GMT (it matches the activity's `startTimeGMT`).
- Only ACTIVE sets are stored, with `set_index = messageIndex`. 0-rep sets are stored and ignored by stats.
- Exercise mapping is an exact `(category, name)` match. The seeded catalog has no category-only rows, so there is no fallback. Unmatched sets keep `exercise_id` null and count as `unmapped_sets`.
- `perceived_effort` and `feel` store Garmin's `directWorkoutRpe` and `directWorkoutFeel` as given (0 to 100 scales).
- Working sets with no load anywhere in the session (bodyweight) all count. Top set is the heaviest, then most reps. An exercise with no working set gets no stats row.
Status: decided.

## 2026-10-04: Phase 2 test sessions

The account had no real strength history: the Phase 0 bench session (24606995677) is no longer in Garmin. The only other activity is one run. `spikes/seed_test_sessions.py` created 3 private manual strength activities with sets. Garmin accepts `set_activity_exercise_sets` on a manual activity, and every set read back at the right lb.

| Session | Garmin id | Date |
|---|---|---|
| Spotter test 1 | 24607983009 | 2026-09-28 |
| Spotter test 2 | 24607983571 | 2026-09-30 |
| Spotter test 3 | 24607983681 | 2026-10-02 |

Expected stats, computed by hand from coaching rules A.1 and A.2 before syncing (e1RM is `-` everywhere: nothing is curated):

| Session | Exercise | Sets logged (lb x reps) | Top set | Working sets | Reps | Volume (lb) |
|---|---|---|---|---|---|---|
| 1 | Barbell Bench Press | 95x8, 185x5, 185x5, 185x4 | 185 x 5 | 3 | 14 | 2590.0 |
| 1 | Barbell Back Squat | 135x5, 225x5, 225x5 | 225 x 5 | 3 (135 is exactly 60 %) | 15 | 2925.0 |
| 1 | Pull-up | BWx8, BWx8, BWx6 | BW x 8 | 3 | 22 | 0.0 |
| 2 | Barbell Bench Press | 190x5, 190x5, 190x5, 190x0 | 190 x 5 | 3 | 15 | 2850.0 |
| 2 | Dumbbell Row | 60x12, 60x12, 60x10 | 60 x 12 | 3 | 34 | 2040.0 |
| 3 | Barbell Back Squat | 135x5, 245x5, 245x5, 245x3 | 245 x 5 | 3 | 13 | 3185.0 |
| 3 | Barbell Deadlift | 135x5, 225x3, 315x5 | 315 x 5 | 2 | 8 | 2250.0 |

Cleanup: `spikes/seed_test_sessions.py --delete` removed the 3 activities from Garmin and their rows from Neon on 2026-10-04, after the athlete confirmed the check.
Status: done. Neon now holds 1 activity (the run) and no strength history.

## Phase 2 results

Exit criteria met on 2026-10-04, before cleanup:
- `uv run pytest` green (107 tests). The engine has 100 % branch coverage. ruff and mypy are clean.
- `spotter backfill --since 2026-01-01` against Neon: 4 activities (3 strength, 1 run), 24 sets, 0 unmapped, `partial: false`. That is the account's full history.
- `spotter strength-log` shows every set at the logged lb value, and all 7 stats rows match the hand-computed table above.
- A second `spotter sync` reports `skipped: recent`. `--force` re-reads only the 2-day overlap, and row counts stay unchanged (4 / 24 / 7).

The athlete confirmed the check, and the test sessions are deleted. Phase 3 may start.

## 2026-10-04: Phase 3 ships as two PRs

- 3a: daily metrics fixtures, adapter, mapper and sync; `engine.readiness`, `engine.endurance`, and the exercise trend. No server.
- 3b: FastMCP server, auth, the eight read and admin tools, deploy from the repo root, and the phone exit check.
Why: CLAUDE.md keeps PRs to one phase or less. 3a can be checked locally (pytest, a `spotter sync` filling `daily_metrics` in Neon) before anything is deployed.
Status: decided.

## 2026-10-04: `map_exercise` persists through `exercise_aliases`

Why: sync rewrites the sets of recent activities and maps them by exact `(garmin_category, garmin_name)`. A mapping stored only on `performed_sets` would be undone by the next sync.
Decision: a new table, `exercise_aliases (garmin_category, garmin_name) -> exercise_id`, in one Alembic migration. Sync tries the exact catalog match first, then the alias. `map_exercise` writes the alias, remaps the stored sets with that pair, and rebuilds the stats of the affected activities.
Alternatives rejected: a new `exercises` row per unknown pair (history stays split from the real exercise); keeping the old `exercise_id` on rewrite (activities not yet stored still arrive unmapped).
Status: decided. SPEC section 7 updated.

## 2026-10-04: Daily metrics sync window

Finding: in garminconnect 0.3.17, HRV, Body Battery, max metrics and body composition take a date range. Sleep, training readiness, resting HR and training status take one date. Re-reading 14 days of per-day calls on every sync is about 56 calls. The call set was revised after recording fixtures (see "Daily metrics call set" below).
Decision:
- Range calls cover the last 14 days on every sync.
- Per-day calls cover the last 3 days, plus any day in the 14-day window with no stored row.
- Metrics share the activity sync's lock and its 60 s budget. Activities run first. Metrics keep their own `sync_state` row (`daily_metrics`).
- `spotter backfill` syncs metrics for the last 28 days by default (the longest readiness window), not the whole `--since` range.
Why: values settle within a day or two after sleep. This cuts a sync to about 12 to 15 calls after the first one and lowers the 429 risk.
Status: decided. SPEC section 9 updated.

## 2026-10-04: Endurance interference window

Rule A.7 says "in the 48 h before" a planned lower-body day. Planned days have a date, not a time.
Decision: count activities whose local date is the planned date or one of the 2 dates before it. Same-day activities count because a morning run can precede an evening lift.
Status: decided.

## 2026-10-04: Phase 3 server details

- OAuth client storage moves to a new `oauth_store` table owned by `PostgreSQLStore`. The athlete reconnects the connector once. `spike_kv_store` is dropped after the switch.
- The Vercel project's root directory moves from `spikes/vercel_hello` to the repo root. A preview deploy confirms the `src/` package installs from `uv.lock` before production.
- Phase 3 ships a short interim instruction text (snapshot first, lb only, read-only). Part B ships in Phase 6.
Status: decided.

## 2026-10-04: Phase 3 exit check uses seeded test sessions

Why: Neon holds one run and no strength history, so "summarize my last 4 weeks of training" would have almost nothing to check.
Decision: extend `spikes/seed_test_sessions.py` to create about 6 to 8 private strength sessions spread over 4 weeks. Run the exit check against them, then delete them with `--delete`, as in Phase 2. Daily metrics come from the athlete's watch.
Status: decided.

## 2026-10-04: Daily metrics call set

Finding (fixtures recorded 2026-10-04): the daily summary (`get_user_summary`) carries resting HR, Body Battery high and low, and average stress for the day. The morning readiness entry carries acute load.
Decision:
- Range calls: `get_hrv_data_range`, `get_max_metrics_range`, `get_body_composition`.
- Per-day calls: `get_sleep_data`, `get_training_readiness`, `get_user_summary`.
- Dropped: `get_rhr_day` and `get_body_battery` (covered by the summary), `get_morning_training_readiness` (it filters the readiness list client-side, so Spotter picks the `AFTER_WAKEUP_RESET` entry itself, else the first).
- Deferred: `get_training_status`. Every field was null on this account, so the populated shape of load balance is unknown. `load_balance` stays null until a recording shows it. No engine rule reads it.
- A negative `averageStressLevel` means not enough data and is stored as null. HRV status `NONE` (baseline still building) is stored as null, so readiness reports it as unknown.
Status: decided. SPEC section 8.2 updated.

## 2026-10-04: Metric fixtures carry fake numbers

Why: the repo is public, and the fixtures can be tied to the athlete through the repo owner. The athlete chose fake values over real ones.
How: `anonymize_fixture.py --metrics` keeps Garmin's real structure, enum labels and shifted timestamps. Every other number becomes 0, time series are emptied, and the fields Spotter maps get fixed fake values. It also replaces every user or profile id key and nested activity names, and refuses to write a file if a real id survives. Two leaks were caught before any commit: the user id under `userId`/`userProfilePK`-style keys in 7 files, and an event name inside Body Battery events.
Remaining real data: the time of day in sleep timestamps (dates shifted by 364 days).
Status: decided.

## 2026-10-04: Onboarding gaps in the metric fixtures

The watch started recording on 2026-10-02, so Garmin was still building baselines. HRV `baseline`, readiness `score` and `acuteLoad`, and all of `get_training_status` were null.
Effect: `hrv_baseline_low/high` map from `baseline.balancedLow/balancedUpper`, Garmin's documented shape, tested with a constructed entry. Re-record with `spikes/record_fixtures.py --metrics` once HRV status leaves `NONE` (about 3 weeks of wear), confirm the baseline keys, and add the training status mapping.
Status: open. Revisit before Phase 4 relies on readiness.

## Phase 3a results

Checked on 2026-10-04:
- `uv run pytest` green (197 tests). The engine has 100 % branch coverage. ruff and mypy are clean.
- `spotter sync --force` against Neon: 14 metric days, all fetched per-day, `partial: false`, 9.3 s in total. Data starts 2026-10-02, when the watch started recording. Values are plausible (resting HR 58 to 64, overnight HRV 31 to 32 ms, sleep scores 64 and 77). HRV status and readiness are null, as expected while onboarding.
- A second `sync` reports `skipped: recent`. `--force` fetches only the last 3 days per-day.

## 2026-10-04: Snapshot carries a 28-day strength rollup

Why: the Phase 3 exit check asks for a 4-week summary. The last 3 sessions are not enough, and making Claude find exercise ids through search first is fragile.
Decision: `get_training_snapshot` adds `strength_28d`: session count and dates, and per exercise its id, name, exposures, last top set, working sets, volume and trend. No ninth tool. The athlete chose this over a new tool.
Status: decided. SPEC section 11.1 updated.

## 2026-10-04: Phase 3b server details

- Error policy: the snapshot never fails on Garmin. A Garmin error goes into `sync.error`, a held lock into `sync.skipped: locked`, and the rest is read from stored data. `sync_garmin` and `map_exercise` raise tool errors.
- The snapshot checks the 10-minute rule before opening a Garmin session, so repeat snapshots make no Garmin call.
- `sync_garmin(since)` reaches back at most 60 days. Older history uses `spotter backfill` locally, which has no budget.
- `map_exercise` refuses a pair already in the catalog and is idempotent.
- `search_exercises(equipment)` matches the curated `load_type`, or the name while an exercise is uncurated. Every seeded row is uncurated, so names are what work today.
- Distances are shown in km.
- The production app lives in `spotter.mcp.asgi`. `spotter.mcp.server` builds it from settings, so importing it needs no secrets. `spotter.mcp.dev` is a no-auth server for the local inspector only.
- `vercel.json` sets `maxDuration` 120 s: the 60 s sync budget plus metrics and reads. `.vercelignore` keeps spikes and tests out of CLI uploads.
- Commits 2 to 4 of the plan landed as one: the server module imports both tool groups, so separate commits would not each import cleanly.
Status: decided.
