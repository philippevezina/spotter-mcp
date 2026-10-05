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

## 2026-10-04: Phase 3 test sessions

`spikes/seed_test_sessions.py` now seeds 8 private strength sessions over 4 weeks, alternating upper and lower days. Warm-ups stay under 60 % of the top set. Session 7 has a missed rep on the last bench set. Expected values, computed by hand from rules A.1 and A.2 and cross-checked with `engine.sets.summarize` before seeding. e1RM is `-` everywhere: nothing is curated.

| # | Date | Exercise | Top set | Working sets | Reps | Volume (lb) |
|---|---|---|---|---|---|---|
| 1 | 2026-09-09 | Barbell Bench Press | 175 x 5 | 3 | 15 | 2625.0 |
| 1 | 2026-09-09 | Dumbbell Row | 55 x 12 | 3 | 36 | 1980.0 |
| 1 | 2026-09-09 | Pull-up | BW x 8 | 3 | 22 | 0.0 |
| 2 | 2026-09-12 | Barbell Back Squat | 205 x 5 | 3 | 15 | 3075.0 |
| 2 | 2026-09-12 | Barbell Deadlift | 255 x 5 | 2 | 10 | 2550.0 |
| 3 | 2026-09-16 | Barbell Bench Press | 180 x 5 | 3 | 15 | 2700.0 |
| 3 | 2026-09-16 | Dumbbell Row | 60 x 12 | 3 | 36 | 2160.0 |
| 3 | 2026-09-16 | Pull-up | BW x 8 | 3 | 23 | 0.0 |
| 4 | 2026-09-19 | Barbell Back Squat | 215 x 5 | 3 | 15 | 3225.0 |
| 4 | 2026-09-19 | Barbell Deadlift | 275 x 5 | 2 | 10 | 2750.0 |
| 5 | 2026-09-23 | Barbell Bench Press | 185 x 5 | 3 | 15 | 2775.0 |
| 5 | 2026-09-23 | Dumbbell Row | 60 x 12 | 3 | 34 | 2040.0 |
| 5 | 2026-09-23 | Pull-up | BW x 9 | 3 | 24 | 0.0 |
| 6 | 2026-09-26 | Barbell Back Squat | 225 x 5 | 3 | 15 | 3375.0 |
| 6 | 2026-09-26 | Barbell Deadlift | 295 x 5 | 2 | 10 | 2950.0 |
| 7 | 2026-09-30 | Barbell Bench Press | 185 x 5 | 3 | 14 | 2590.0 |
| 7 | 2026-09-30 | Dumbbell Row | 65 x 10 | 3 | 30 | 1950.0 |
| 7 | 2026-09-30 | Pull-up | BW x 9 | 3 | 26 | 0.0 |
| 8 | 2026-10-03 | Barbell Back Squat | 235 x 5 | 3 | 15 | 3525.0 |
| 8 | 2026-10-03 | Barbell Deadlift | 315 x 5 | 2 | 10 | 3150.0 |

Expected `strength_28d` (check run 2026-10-03 to 2026-10-06): 8 sessions.

| Exercise | Exposures | Working sets | Volume (lb) | Trend |
|---|---|---|---|---|
| Barbell Back Squat | 4 | 12 | 13200.0 | top set 205 → 235 lb, up |
| Barbell Bench Press | 4 | 12 | 10690.0 | top set 175 → 185 lb, up |
| Barbell Deadlift | 4 | 8 | 11400.0 | top set 255 → 315 lb, up |
| Dumbbell Row | 4 | 12 | 8130.0 | top set 55 → 65 lb, up |
| Pull-up | 4 | 12 | 0.0 | top-set reps 8 → 9, up |

Cleanup: `--delete` removed the 8 activities from Garmin and their rows from Neon on 2026-10-04, after the athlete confirmed the check.
Status: done. Neon again holds 1 activity (the run) and no strength history.

## Phase 3b results

Checked on 2026-10-04:
- `uv run pytest` green (222 tests). The engine has 100 % branch coverage. ruff and mypy are clean on `src/spotter`.
- Migration `0002` (`exercise_aliases`) applied to Neon.
- A preview deploy installed from `uv.lock` and imported the app, then stopped at `ServerConfigError`, as expected: previews do not get the Production-only env vars.
- Production serves the new server: OAuth metadata lists only `read:user`, and `POST /mcp` without a token returns 401. The connector was reconnected once; `oauth_store` holds the new registration and `spike_kv_store` is dropped.
- Seeded 8 sessions, then `spotter backfill --since 2026-09-01`: 9 activities, 0 unmapped sets, `partial: false`. All 20 stats rows match the "Phase 3 test sessions" table.

Exit check: from the phone, "summarize my last 4 weeks of training" returned 8 sessions from Sep 7 to Oct 4, every lift with the right start and end top set, the Oct 3 run (10.2 km, 64 min, aerobic training effect 5.0, load about 417), and readiness data on 3 of 28 days, all green. Every number matched Neon. Phase 3 exit criteria met. Phase 4 may start.

Two gaps found in Claude's answer, both fixed in the snapshot after the check:
- It said "no stalls" because `flags.stalls` was `[]`, but stall detection only arrives in Phase 4. `stalls` is now `null`, with a note telling Claude not to report stalls as absent.
- It said it could not see earlier running without calling `get_endurance_load`. The snapshot description now points to that tool for history beyond 7 days.

## 2026-10-04: Phase 4 ships as two PRs

- 4a: `engine.progression`, `engine.plan_eval`, `engine.volume`, `units.floor_lb`, and sync linking. Checked locally.
- 4b: the ten planning tools, stalls in the snapshot, interim instructions, deploy and the exit check.
Status: decided.

## 2026-10-04: Phase 4 progression decisions

Nothing is pushed until Phase 5, so `prescribed_sets` stay empty and every exposure is judged against `plan_exercises`. The engine takes optional per-set targets, so Phase 5 can pass prescribed targets without new rules.

- **A.3 double_progression step 1 was inverted.** It said "RIR ≤ `target_rir`", which blocked an easy session and allowed a grinder. Now: add load unless RIR < `target_rir − 1`. coaching-rules.md is corrected.
- **Model scope.** `plans.progression_model` applies to `main` lifts. `secondary` and `accessory` use double_progression (A.3 defaults). `progression_override {"model": ...}` on a plan exercise wins over both.
- **Working weight.** The top working-set weight. Progression reads the sets done at it, so back-off sets are ignored. Bodyweight work reads every working set. Only the first `sets` sets count.
- **Hit and miss.** Miss: any counted set below `rep_min`. Linear target is `rep_max`; reps between `rep_min` and `rep_max` on a linear lift hold and are not a miss. Double and rir_based count a hit at `rep_min`.
- **Short session.** Fewer sets than planned at the working weight holds the prescription. It is not a miss, and the stall streak skips it. A time-short day is not a strength failure.
- **Stall streak.** Consecutive misses at the same working weight, counted only from exposures on or after the plan start. Deload and short exposures are skipped. A hit, a hold or a weight change ends it. 2 holds with `stall_warning`; 3 or more resets (−10 %, rounded down) with `stall`. Re-derived from history on every call, never stored.
- **First exposure in a plan at a new rep range.** When the last exposure's reps fall outside the new range, the load comes from the best Epley estimate over working sets of 1 to 10 reps in the last 12 weeks, solved for `rep_min + target_rir` reps and rounded down (`estimate_from_history`). This uses any exercise, not only `e1rm_eligible` ones, because it only estimates a starting load. Bodyweight history, or history above 10 reps only, returns `needs_calibration`.
- **Targets.** Double step 2 targets each set at its reps + 1, capped at `rep_max`. Step 3 ("same targets") uses `rep_min` on every set, since Phase 4 has no stored targets. A hold keeps the performed reps, clamped to the range.
- **Linear step.** +5 lb upper body, +10 lb lower body (pattern squat, hinge or lunge), rounded to the increment and at least one increment.
- **Rounding.** Increases round to the nearest increment; holds and decreases round down, so an off-grid logged weight never creeps up on a hold. Barbell loads never go below 45 lb.
- **Bodyweight.** Reps-only double progression. At `rep_max` on every set the prescription holds with `bodyweight_at_rep_max`, so Claude can suggest a harder variation or added load. An exercise with no increment behaves the same.
- **rir_based.** "Within target" is `target_rir − 1` to `target_rir`. "Second time in a row" means the previous exposure in this plan, at the same weight, was also below `target_rir − 1`.
- **Deload.** `plans.deload_week` and any `record_adjustment(kind="deload", payload={"week_no": N})` mark deload weeks. Week N runs from `start_date + 7(N − 1)` for 7 days. Exposures in a deload week are left out of progression history. A deload proposal starts from the last non-deload weight: sets halved (rounded up), load −10 % (nearest increment), `target_rir` +2.
- **Readiness modifiers (A.6)** apply only when the session is today, since readiness is unknown for other dates. Amber and red keep the last weight and reps whenever the proposal would progress; resets and holds pass through. Red also adds 1 to `target_rir` and removes a set from main and secondary lifts (minimum 2). Endurance interference (A.7) stops load increases on lower-body main lifts only. On a deload week only the deload rules apply.
Status: decided.

## 2026-10-04: Phase 4 planning decisions

- **Scheduling.** Each plan day runs once a week (the schema's `UNIQUE (plan_day_id, week_no)`), so `sessions_per_week` equals the number of days. Every day needs a distinct `preferred_weekday`. Week 1 is the 7 days starting at `start_date`. `start_date` may be up to 28 days back, so a plan can start retroactively and link sessions already done.
- **Muscle vocabulary** for curation and volume: chest, back, shoulders, biceps, triceps, quads, hamstrings, glutes, calves, core. Coarse on purpose, so the 8 to 20 weekly sets range from A.8 stays meaningful.
- **Goals.** e1RM targets are stored in kg with `target_unit = "e1rm"`, like every other weight. Tools take and return lb. SPEC's `lb_e1rm` comment is superseded.
- **Closing or replacing a plan** sets its open future sessions to `skipped`. Phase 5 must also unschedule pushed ones in Garmin.
Status: decided.

## 2026-10-04: Sync links sessions by date from Phase 4

Why: `evaluate_plan` needs completed sessions for adherence, and Phase 4 has no other way to know a session happened.
Decision: at the end of every sync, `link_sessions` attaches each open session (`planned` or `pushed`, no activity) in the window to the earliest strength activity of its local date that no session holds yet, and sets it `completed`. Sessions of one date go in plan day order. `activate_plan` runs it too, for retroactive starts. Phase 5 adds the workout-id match ahead of the date match.
Status: decided. SPEC sections 9 and 15 updated.

## 2026-10-04: Phase 4b tool details

- **Close or replace skips every open session**, past ones too, not only future ones. A past open session was missed anyway, and `skipped` still counts as missed in adherence. Otherwise it would stay in `missed_sessions` after the plan ends. This refines "Closing or replacing a plan" above.
- **`end_criteria.max_weeks` defaults to the plan's `weeks`**, not the A.10 default of 6. Otherwise an 8-week block Claude chose on purpose would be told to end at week 6. The other three criteria keep the A.10 defaults, and the full object is stored.
- **Comparable recent history** (A.8, new-plan volume of 10 to 12 sets): any exercise in the plan has a performed set in the last 12 weeks.
- **Proposal history** is the 12 weeks of activities dated before the session. Endurance interference applies on any date and uses the activities stored so far. Readiness applies only when the session is today (4a decision).
- **An exercise on two days of a plan**: stalls in the snapshot and `evaluate_plan` use its first occurrence (day order, then position).
- **Curation defaults.** Increment is 5 lb for barbell, dumbbell (per hand), machine, cable and bodyweight_plus (the logged load is the added weight). Bodyweight has none. `e1rm_eligible` defaults to true for barbell and dumbbell lifts with a compound pattern (not isolation, core or carry). Changing it rebuilds that exercise's stats.
- **`activate_plan`** takes a start date up to 28 days back or ahead. Each day's session falls on its weekday inside week N's 7-day window. Calling again with the same start returns the schedule unchanged. Any other call on a non-draft plan is an error.
- **`create_plan` is not deduplicated.** Each call saves a new draft, and drafts have no effect until activated.
- **Snapshot stalls** list current miss streaks of 2 or more in the active plan. The list is `[]` (not null) once a plan is active. With no plan it is `[]` with the note "No active plan."
- **Commits.** The write and calculation tools, the shared loaders and the snapshot stalls landed in one commit. The server imports all of them, and one test module covers them end to end.
Status: decided.

## 2026-10-04: Phase 4 test sessions

`spikes/seed_test_sessions.py --set phase4` seeds 7 private strength sessions. Warm-ups stay under 60 % of the top set. The plan starts on Monday 2026-09-14 and runs 6 weeks, with `progression_model` linear. Upper runs on Tuesday: OHP main 3×5, Bench secondary 3×6–8, DB Row accessory 3×8–12. Lower runs on Thursday: Squat main 3×5, Deadlift secondary 3×8–10 at RIR 2. Every lift uses barbell or dumbbell increments of 5 lb. Run the check on 2026-10-05, so the week-4 sessions (Tuesday 10-06, Thursday 10-08) are not today and readiness is `not_applicable`. The Oct 3 run is outside the Lower day's 48 h.

| # | Date | Session | Sets (lb × reps) |
|---|---|---|---|
| 1 | 2026-09-07 | before the plan | Deadlift 135×5, 315×5, 315×5 |
| 2, 4 | 09-15, 09-22 | Upper W1, W2 | OHP 45×5, 115×5,4,4. Bench 95×8, 185×7,7,6. DB Row 60×9,9,8 |
| 6 | 09-29 | Upper W3 | OHP 45×5, 115×5,4,4. Bench 95×8, 185×8,8,8. DB Row 60×10,10,9 |
| 3, 5, 7 | 09-17, 09-24, 10-01 | Lower W1–W3 | Squat 95×5, then 205, 215, 225 × 5,5,5 |

Expected proposals, computed by hand from A.3, A.4 and the Phase 4 decisions:

| # | Scenario | Lift (session) | Expected | Why |
|---|---|---|---|---|
| 1 | Linear hit, lower body | Squat (Lower W4) | 235 × 5,5,5, `linear:+10lb` | 225 hit 5,5,5; squat pattern adds 10 lb |
| 2 | Double progression, add reps | DB Row (Upper W4) | 60 × 11,11,10, `double_progression:+1rep` | All sets ≥ 8, not all at 12: each set +1 |
| 3 | Double progression, add load | Bench (Upper W4) | 190 × 6,6,6, `double_progression:+5lb` | All sets at rep_max 8, RIR unknown |
| 4 | Stall reset | OHP (Upper W4) | 100 × 5,5,5, `reset:-10%`, flag `stall` | 3 misses at 115 in the plan; 115 × 0.9 = 103.5, rounded down to 100 |
| 5 | Estimate from history | Deadlift (Lower W4) | 275 × 8,8,8, `estimate_from_history` | 315 × (1 + 5/30) = 367.5; ÷ (1 + 10/30) = 275.6, rounded down to 275 |

Also expected on 2026-10-05:
- `evaluate_plan` returns `continue`, week 4 of 6, adherence 4/4 = 1.0 over 09-21 to 10-04, reset_lifts [OHP] (1 < 2).
- The snapshot shows OHP `stall` with 3 misses.
- `activate_plan` links 6 sessions.

The same scenarios pass as `tests/mcp/test_planning.py::test_exit_scenarios` against the engine.
Cleanup:
- `--set phase4 --delete` unlinks the scheduled sessions and removes the activities.
- `--delete-plan <id>` removes the test plan, only if the athlete agrees.
Status: done. Live check passed (see "Phase 4 results").

## Phase 4 results

Checked on 2026-10-04, in the evening in Toronto. The check was planned for 10-05.
- `uv run pytest` is green (349 tests). The engine has 100 % branch coverage. ruff and mypy are clean. PR #8 is merged and deployed to production.
- `seed_test_sessions.py --set phase4` created 7 private activities: 24608922869 (09-07), 24608922938 (09-15), 24608923030 (09-17), 24608923136 (09-22), 24608923231 (09-24), 24608923333 (09-29), 24608923425 (10-01). Every set read back at the seeded lb value.
- `spotter backfill --since 2026-09-01`: 8 activities (the 7 sessions plus one run), 0 unmapped sets, `partial: false`.

Exit check, from the phone:
- Claude curated the 5 lifts, created plan 1 as a draft, and activated it from 2026-09-14 after the athlete agreed. Activation linked 6 sessions (weeks 1 to 3) and scheduled 6 more, ending 2026-10-25.
- `propose_next_session` for Upper W4 (10-06) and Lower W4 (10-08) returned exactly the "Phase 4 test sessions" table:
  - Squat: 235 × 5.
  - DB Row: 60 × 11/11/10.
  - Bench: 190 × 6.
  - OHP: 100 × 5 with `stall` and 3 misses.
  - Deadlift: 275 × 8 from an e1RM of 367.5.
- Readiness was `not_applicable`, and there were no interference flags.
- Claude explained each rule correctly and asked before keeping the OHP reset.
- `evaluate_plan` was not called from the phone. Running the server's `evaluate` read-only against Neon on 10-04 gave `continue`, week 3 of 6 (week 4 on 10-05, as noted), adherence 4/4 = 1.0, and OHP as the only reset lift. The snapshot showed the OHP `stall`.

Phase 4 exit criteria met. Phase 5 may start.

Cleanup on 2026-10-04, after the athlete confirmed:
- `--set phase4 --delete` removed the 7 activities from Garmin and Neon, after unlinking 6 sessions.
- `--delete-plan 1` removed the test plan.
- Neon again holds 1 activity (the run), no plans and no strength history.
- The 5 lifts stay curated.

Notes from the conversation:
- Claude read "it should link 6 sessions" as a session count and asked about it before activating. That was reasonable: the prompt was ambiguous.
- `create_plan` has no start date, so Claude said it "couldn't set" one. Activation is a separate step by design. Claude handled it correctly.
- Claude added extra secondary muscles while curating (core on squat, deadlift and OHP; chest on OHP; shoulders on row). These affect only the volume warnings.
