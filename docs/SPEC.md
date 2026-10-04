# Spotter: Specification

Status: draft v1, 2026-10-04
Owner: Philippe

## 1. Goal

Let Claude plan strength training and push it to Garmin Connect, from any Claude app, including the phone.

Claude must be able to:

1. Build a plan (a mesocycle) for a set number of sessions, from current ability and a target goal.
2. Decide how long the plan should run and when it is time to change it.
3. Propose weights, sets, reps and rest for each session from previous sessions, with progressive overload.
4. Use other Garmin data (running, cycling, HRV, sleep, readiness, training load) to adjust proposals.
5. Push each session to Garmin Connect as a strength workout with target weights, scheduled on the calendar.

The user logs reps and weight on the watch during each session. Garmin is the source of truth for what was done. This system is the source of truth for what was planned and why.

## 2. Non-goals (v1)

- No web UI. Claude is the only interface.
- No multi-user support. One athlete, one Garmin account.
- No scheduled or background jobs. Everything runs when a Claude conversation calls a tool.
- No nutrition, no running plan generation. Running and cycling are inputs, not outputs.

## 3. Architecture

```
Claude (phone / desktop / web)
        │  custom connector, Streamable HTTP, OAuth
        ▼
Vercel Function (Python, Hobby tier)  https://spotter-mcp.vercel.app/mcp
   ├── FastMCP server, stateless HTTP
   ├── Auth: FastMCP GitHubProvider, restricted to one GitHub login
   ├── Tools (section 7)
   ├── Engine: progression, readiness, plan evaluation (plain code)
   └── Garmin adapter (python-garminconnect, pinned)
        │
        ▼
Neon Postgres (free tier)       Garmin Connect (unofficial API)
```

### 3.1 Why this shape

- **Remote MCP is required.** The Claude mobile app only uses remote MCP servers added as custom connectors.
- **Vercel Hobby** gives free HTTPS on a `*.vercel.app` domain and no server to maintain. Python functions are supported. Max duration is 300 s on Hobby with Fluid compute.
- **Vercel functions have no persistent disk.** SQLite on local disk is not an option. All state, including refreshed Garmin tokens and OAuth client registrations, lives in Postgres.
- **Neon free tier** (1 GB storage per project, scales to zero after 5 minutes) is plenty for one athlete.
- **Portability.** Keep the app a plain ASGI app with no Vercel-specific code outside `api/index.py`. If Vercel stops working (Garmin blocks its IPs, Python MCP issues), the same code deploys to Oracle Always Free with Docker and Kamal. Postgres stays on Neon either way.

### 3.2 Known risks

| Risk | Mitigation |
|---|---|
| Garmin login rate limits (429, multi-day blocks reported) | Log in once locally with `spotter bootstrap-login`. Store tokens encrypted in Postgres. Never call login from the server. Refresh only. |
| Garmin blocks or throttles cloud IPs | Phase 0 test from a real Vercel deployment. Fallback: Oracle VM. |
| Unofficial API changes | Pin `garminconnect==0.3.17`. All Garmin calls go through `spotter.garmin` only. Contract tests against recorded fixtures. |
| Python MCP on Vercel is less common than TypeScript | Phase 0 deploys a hello-world FastMCP server with auth and connects it from the phone before any real work. |
| Function timeout (300 s) | Incremental sync with a per-call budget. Historical backfill runs locally via CLI. |
| Claude calls tools in parallel | Sync takes a Postgres advisory lock. Writes are idempotent. |

## 4. Tech stack

- Python 3.12, managed with `uv`.
- `fastmcp==4.0.10` (pinned) for the MCP server, `http_app(stateless_http=True)`.
- `garminconnect==0.3.17` (pinned) with the `workout` extra (pydantic).
- Postgres via SQLAlchemy 2.0 Core and `psycopg` 3. Migrations with Alembic.
- `cryptography` (Fernet) for encrypting Garmin tokens at rest.
- `pytest`, `ruff`, `mypy` (strict on `spotter.engine`).
- Local Postgres for development via Docker Compose.

## 5. Repository layout

```
spotter-mcp/
  CLAUDE.md
  docs/
    SPEC.md               # this file
    coaching-rules.md     # engine rules + server instructions source
    decisions.md          # results of Phase 0 and later decisions (ADR-lite)
  api/
    index.py              # Vercel entry point: exposes the ASGI app
  src/spotter/
    config.py             # settings from env vars
    units.py              # kg/lb conversion and rounding
    db/
      schema.py           # SQLAlchemy Core tables
      session.py
      seed.py             # exercise catalog seed
    migrations/           # Alembic
    garmin/
      client.py           # only module that imports garminconnect
      errors.py           # domain errors (auth expired, 429, unavailable, bad token key)
      tokens.py           # load/save encrypted tokens in Postgres
      session.py          # token-backed session that saves a refresh on exit
      mappers.py          # Garmin JSON -> domain rows
      workouts.py         # domain prescription -> Garmin StrengthWorkout
    sync/
      sync.py             # incremental sync with time budget and lock
      stats.py            # exercise_session_stats derivation
    engine/
      rules.py            # every threshold from coaching-rules.md Part A
      types.py
      sets.py             # working sets and per-exercise session summary
      e1rm.py
      progression.py
      readiness.py
      endurance.py
      plan_eval.py
    mcp/
      server.py           # FastMCP app, auth, tool registration
      auth.py             # GitHub provider + login allowlist
      instructions.md     # shipped server instructions (from coaching-rules.md Part B)
      tools/              # one module per tool group
    cli.py                # bootstrap-login, import-tokens, garmin-check, seed-exercises,
                          # sync, backfill, rebuild-stats, strength-log
  tests/
    fixtures/garmin/      # recorded, anonymized Garmin responses
  spikes/                 # Phase 0 throwaway scripts
  alembic.ini
  docker-compose.yml
  vercel.json
  pyproject.toml
```

## 6. Units

- The user thinks in **pounds**. Every value shown to Claude or the user is in lb.
- **Storage is kilograms** (`NUMERIC(7,3)`), because Garmin stores weight in metric. Workout steps: `weightValue` in kg rounded to 0.01 under the `kilogram` unit tag (grams do not display on the watch). Performed sets: `weight` in grams. See decisions.md.
- `spotter.units` owns all conversion. Rule: round to the achievable lb increment first, then convert to kg for storage and for Garmin. Convert back and round to 0.5 lb for display.
- Each exercise has `increment_lb` (smallest real jump): barbell 5 lb, dumbbell 5 lb per hand, machine stack configurable.
- Round trip confirmed in Phase 0: targets pushed as 2-decimal kg show as the right lb on the watch, and a set logged at 190 lb syncs back as 190.0 lb after rounding.

## 7. Database schema (Postgres)

Timestamps are `timestamptz` in UTC. Dates are `date` in the athlete's local time zone (America/Toronto).

```sql
-- Reference ---------------------------------------------------------------
CREATE TABLE athlete (
  id                 smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  display_units      text NOT NULL DEFAULT 'lb',
  time_zone          text NOT NULL DEFAULT 'America/Toronto',
  bodyweight_kg      numeric(6,2),
  training_age_years numeric(4,1),
  available_days     jsonb,          -- ["mon","wed","fri"]
  session_minutes    int,
  equipment          jsonb,          -- {"barbell":true,"plates_lb":[45,35,25,10,5,2.5],...}
  limitations        text,
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE exercises (
  id                bigserial PRIMARY KEY,
  display_name      text NOT NULL,
  garmin_category   text NOT NULL,
  garmin_name       text NOT NULL DEFAULT '',   -- '' means category only
  curated           boolean NOT NULL DEFAULT false,
  movement_pattern  text,   -- squat|hinge|push_h|push_v|pull_h|pull_v|lunge|carry|core|isolation
  primary_muscles   jsonb,
  secondary_muscles jsonb,
  load_type         text,   -- barbell|dumbbell_each|machine|cable|bodyweight|bodyweight_plus
  increment_lb      numeric(5,2),
  e1rm_eligible     boolean NOT NULL DEFAULT false,
  UNIQUE (garmin_category, garmin_name)
);
-- Seeded from garminconnect.exercises (1,527 rows). Curated fields are filled
-- when an exercise is first used in a plan.

CREATE TABLE goals (
  id                 bigserial PRIMARY KEY,
  kind               text NOT NULL,   -- strength|hypertrophy|hybrid|endurance_support
  description        text NOT NULL,
  target_exercise_id bigint REFERENCES exercises(id),
  target_value       numeric(8,2),
  target_unit        text,            -- lb_e1rm|reps|...
  target_date        date,
  status             text NOT NULL DEFAULT 'active',  -- active|achieved|dropped
  created_at         timestamptz NOT NULL DEFAULT now()
);

-- Plan (intent) -----------------------------------------------------------
CREATE TABLE plans (
  id                bigserial PRIMARY KEY,
  goal_id           bigint REFERENCES goals(id),
  name              text NOT NULL,
  status            text NOT NULL DEFAULT 'draft',  -- draft|active|completed|abandoned
  start_date        date,
  planned_end_date  date,
  actual_end_date   date,
  weeks             int NOT NULL,
  sessions_per_week int NOT NULL,
  deload_week       int,               -- null = no planned deload
  progression_model text NOT NULL,     -- double_progression|linear|rir_based
  end_criteria      jsonb NOT NULL,    -- see coaching-rules.md A.10
  rationale         text NOT NULL,
  outcome_summary   text,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_active_plan ON plans ((status)) WHERE status = 'active';

CREATE TABLE plan_days (
  id              bigserial PRIMARY KEY,
  plan_id         bigint NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
  label           text NOT NULL,     -- "Lower A"
  day_order       int NOT NULL,
  preferred_weekday text,
  UNIQUE (plan_id, day_order)
);

CREATE TABLE plan_exercises (
  id                   bigserial PRIMARY KEY,
  plan_day_id          bigint NOT NULL REFERENCES plan_days(id) ON DELETE CASCADE,
  exercise_id          bigint NOT NULL REFERENCES exercises(id),
  position             int NOT NULL,
  superset_group       text,
  role                 text NOT NULL DEFAULT 'accessory',  -- main|secondary|accessory
  sets                 int NOT NULL,
  rep_min              int NOT NULL,
  rep_max              int NOT NULL,
  target_rir           int NOT NULL,
  rest_s               int NOT NULL,
  progression_override jsonb,
  UNIQUE (plan_day_id, position)
);

CREATE TABLE scheduled_sessions (
  id                 bigserial PRIMARY KEY,
  plan_day_id        bigint NOT NULL REFERENCES plan_days(id) ON DELETE CASCADE,
  week_no            int NOT NULL,
  scheduled_date     date NOT NULL,
  status             text NOT NULL DEFAULT 'planned',  -- planned|pushed|completed|skipped|moved
  garmin_workout_id  bigint,
  garmin_schedule_id bigint,
  activity_id        bigint REFERENCES activities(garmin_activity_id),
  UNIQUE (plan_day_id, week_no)
);

CREATE TABLE prescribed_sets (
  id                   bigserial PRIMARY KEY,
  scheduled_session_id bigint NOT NULL REFERENCES scheduled_sessions(id) ON DELETE CASCADE,
  exercise_id          bigint NOT NULL REFERENCES exercises(id),
  set_no               int NOT NULL,
  target_reps          int NOT NULL,
  target_weight_kg     numeric(7,3),     -- null for bodyweight
  target_rir           int,
  rest_s               int,
  rule_fired           text,             -- e.g. "double_progression:+5lb"
  UNIQUE (scheduled_session_id, exercise_id, set_no)
);

-- Actuals (from Garmin) ---------------------------------------------------
CREATE TABLE activities (
  garmin_activity_id bigint PRIMARY KEY,
  type               text NOT NULL,      -- strength_training|running|cycling|...
  start_time         timestamptz NOT NULL,
  local_date         date NOT NULL,
  duration_s         int,
  distance_m         numeric(10,1),
  elevation_gain_m   numeric(7,1),
  avg_hr             int,
  max_hr             int,
  training_load      numeric(7,1),
  aerobic_te         numeric(3,1),
  anaerobic_te       numeric(3,1),
  perceived_effort   int,
  feel               int,
  raw_json           jsonb NOT NULL,
  synced_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX activities_date ON activities (local_date);

CREATE TABLE performed_sets (
  id              bigserial PRIMARY KEY,
  activity_id     bigint NOT NULL REFERENCES activities(garmin_activity_id) ON DELETE CASCADE,
  set_index       int NOT NULL,
  exercise_id     bigint REFERENCES exercises(id),   -- null until mapped
  garmin_category text,
  garmin_name     text,
  reps            int,
  weight_kg       numeric(7,3),
  duration_s      numeric(7,1),
  start_time      timestamptz,
  UNIQUE (activity_id, set_index)
);

CREATE TABLE daily_metrics (
  local_date         date PRIMARY KEY,
  resting_hr         int,
  hrv_overnight_ms   int,
  hrv_status         text,
  hrv_baseline_low   int,
  hrv_baseline_high  int,
  sleep_score        int,
  sleep_s            int,
  body_battery_max   int,
  body_battery_min   int,
  training_readiness int,
  acute_load         numeric(7,1),
  load_balance       jsonb,
  stress_avg         int,
  vo2max_run         numeric(4,1),
  vo2max_bike        numeric(4,1),
  bodyweight_kg      numeric(6,2),
  raw_json           jsonb NOT NULL,
  synced_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE exercise_session_stats (
  activity_id      bigint NOT NULL REFERENCES activities(garmin_activity_id) ON DELETE CASCADE,
  exercise_id      bigint NOT NULL REFERENCES exercises(id),
  local_date       date NOT NULL,
  top_set_weight_kg numeric(7,3),
  top_set_reps     int,
  best_e1rm_kg     numeric(7,3),
  total_reps       int,
  volume_kg        numeric(10,2),
  working_sets     int,
  scheduled_session_id bigint REFERENCES scheduled_sessions(id),
  met_prescription boolean,
  PRIMARY KEY (activity_id, exercise_id)
);

-- Claude's memory ---------------------------------------------------------
CREATE TABLE adjustments (
  id         bigserial PRIMARY KEY,
  plan_id    bigint REFERENCES plans(id),
  created_at timestamptz NOT NULL DEFAULT now(),
  kind       text NOT NULL,   -- load_change|swap|deload|reschedule|volume_change|end_plan|other
  reason     text NOT NULL,
  payload    jsonb
);

CREATE TABLE session_notes (
  id                   bigserial PRIMARY KEY,
  local_date           date NOT NULL,
  scheduled_session_id bigint REFERENCES scheduled_sessions(id),
  text                 text NOT NULL,
  tags                 jsonb,      -- ["pain","energy","form","time"]
  rir_feedback         jsonb,      -- {"<exercise_id>": 2}
  created_at           timestamptz NOT NULL DEFAULT now()
);

-- Infrastructure ----------------------------------------------------------
CREATE TABLE sync_state (
  source         text PRIMARY KEY,   -- activities|daily_metrics
  last_synced_at timestamptz,
  cursor         jsonb
);

CREATE TABLE garmin_tokens (
  id         smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  ciphertext bytea NOT NULL,         -- Fernet-encrypted token store JSON
  updated_at timestamptz NOT NULL DEFAULT now()
);
```

OAuth client storage is not in this schema. py-key-value's `PostgreSQLStore` creates and owns its own table (see decisions.md).

Note: `scheduled_sessions.activity_id` references `activities`, so create `activities` before `scheduled_sessions` in the migration.

## 8. Garmin adapter (`spotter.garmin`)

Only `spotter.garmin.client` imports `garminconnect`. Everything else uses domain types.

### 8.1 Authentication

- `spotter bootstrap-login` (CLI, run on the Mac): prompts for email, password and MFA code if asked (`prompt_mfa` callback), then encrypts the token store and writes it to `garmin_tokens`. Use only when no valid token exists: each login risks a 429.
- `spotter import-tokens <path>` stores an existing token store JSON file, encrypted, without logging in.
- `spotter garmin-check` loads the stored tokens, makes one read call, and saves the token store if it was refreshed.
- Keep one live copy of the token, in `garmin_tokens`. A second copy breaks if Garmin rotates refresh tokens.
- The server loads tokens from `garmin_tokens`, decrypts with `GARMIN_TOKEN_KEY`, and passes the inline JSON as `tokenstore`.
- After each Garmin session, if the token store changed (refresh), re-encrypt and save it.
- The server never calls login with credentials. If tokens are invalid, tools return a clear error: "Garmin session expired. Run `spotter bootstrap-login` locally."
- Token store API (v0.3.17): `garmin.client.dumps()` to save; `Garmin().login(tokenstore=<inline JSON>)` to load. Inline JSON is never auto-saved, so compare `dumps()` before and after to detect a refresh.

### 8.2 Read calls used

`get_activities_by_date`, `get_activity_exercise_sets`, `get_hrv_data_range`, `get_training_readiness`, `get_morning_training_readiness`, `get_sleep_data`, `get_body_battery`, `get_rhr_day`, `get_training_status`, `get_max_metrics_range`, `get_body_composition`.

### 8.3 Write calls used

`upload_strength_workout` (typed `StrengthWorkout`), `update_workout`, `delete_workout`, `schedule_workout`, `unschedule_workout`.

### 8.4 Building workouts

- One exercise block per `plan_exercises` row.
- If every set has the same reps and weight, use `create_strength_set` (repeat group).
- If sets differ (top set plus back-offs, ramps), build individual `create_strength_exercise_step` + `create_strength_rest_step` pairs. A repeat group applies one weight to all sets.
- `step_order` must be unique within the workout. Advance by 3 per repeat group.
- Workout name: `"{plan_day.label} W{week_no} {scheduled_date}"`.
- Put the progression note (e.g. "Squat +5 lb") in the workout description if the API accepts it.

### 8.5 Rate limiting

- Minimum 10 minutes between automatic syncs unless `force=true`.
- Back off on 429 and return a clear message instead of retrying in a loop.

## 9. Sync (`spotter.sync`)

- Triggered by `get_training_snapshot` and `sync_garmin`.
- Takes `pg_try_advisory_lock` on a dedicated connection to the unpooled URL (Neon's pooler is in transaction mode). If another sync holds it, skip and use current data.
- Time budget of 60 s per call. Activities newest first since `sync_state.cursor` minus a 2-day overlap. Stop when the budget is spent and report `partial: true`. The cursor moves only on a complete run. Details in decisions.md ("Sync mechanics").
- For each strength activity: fetch exercise sets, upsert `performed_sets`, map to `exercises` by `(garmin_category, garmin_name)`, recompute `exercise_session_stats`.
- Link a strength activity to the `scheduled_session` on the same local date with status `pushed` (Garmin's workout id on the activity, if present, takes priority). Set status `completed`.
- Daily metrics (Phase 3): last 14 days on each sync (values get revised after sleep), upsert by date.
- Backfill: `spotter backfill --since 2026-01-01` runs locally against the production database, with no time limit.

## 10. Engine (`spotter.engine`)

Plain, deterministic, fully unit-tested code. No LLM calls. Rules live in `docs/coaching-rules.md` Part A and are implemented here.

- `e1rm.py`: Epley, sets of 1 to 10 reps only, ignore warm-up sets (below 60 % of the session top set).
- `progression.py`: given an exercise's prescription and its last N exposures, return the next prescription plus the rule that fired.
- `readiness.py`: classify a day as `green|amber|red` from HRV vs baseline, sleep, training readiness, and Body Battery.
- `endurance.py`: summarize running and cycling load, and flag hard or long sessions within 48 h before a planned lower-body day.
- `plan_eval.py`: adherence, progress toward the goal, stalls, and whether `end_criteria` are met.

Every engine output includes a machine-readable `reasons` list so Claude can explain and override.

## 11. MCP tools

General rules:

- All weights in tool input and output are lb. Convert at the tool boundary.
- Return compact JSON. Never return raw Garmin JSON.
- Include ids so Claude can chain calls.
- Write tools are idempotent where possible and support `dry_run` when they touch Garmin.
- Tool descriptions carry the rules for that tool (e.g. "Call only after the user confirms").

### 11.1 Context (read)

| Tool | Input | Output |
|---|---|---|
| `get_training_snapshot` | `force_sync?` | Sync status; athlete profile; active plan (week X of Y, next session); readiness last 7 and 28 days with today's class; endurance summary and flags; last 3 strength sessions planned vs actual; open flags (stalls, missed sessions, unmapped exercises). |
| `get_exercise_history` | `exercise_id`, `since?`, `limit?` | Per session: date, top set, e1RM, volume, working sets, met_prescription. Plus trend. |
| `get_readiness` | `days` | Daily rows plus baselines. |
| `get_endurance_load` | `days` | Per activity summary plus weekly totals. |
| `get_plan` | `plan_id?` | Plan, days, exercises, schedule with status, adjustments log. |
| `search_exercises` | `query`, `pattern?`, `equipment?` | Matching exercises with Garmin mapping and curated fields. |

### 11.2 Calculation

| Tool | Input | Output |
|---|---|---|
| `propose_next_session` | `scheduled_session_id` | Proposed sets per exercise with reps, weight (lb), RIR, rest, `rule_fired`, readiness and endurance modifiers applied. Does not write. |
| `evaluate_plan` | `plan_id?` | Adherence, per-lift progress, stalls, end_criteria status, recommendation: `continue|adjust|end`. |

### 11.3 Write

| Tool | Input | Effect |
|---|---|---|
| `upsert_athlete_profile` | profile fields | Updates `athlete`. |
| `upsert_goal` | goal fields | Creates or updates a goal. |
| `curate_exercise` | `exercise_id`, curated fields | Sets pattern, muscles, load type, increment. |
| `create_plan` | full plan spec | Validates (exercises curated, weekly hard sets per muscle within rules, rep ranges sane) and saves as `draft`. Returns validation warnings. |
| `activate_plan` | `plan_id`, `start_date` | Ends any active plan as `abandoned` only if `replace=true`. Generates `scheduled_sessions`. |
| `push_session` | `scheduled_session_id`, `sets`, `dry_run?` | Saves `prescribed_sets`, builds the Garmin workout, uploads or updates it, schedules it. Repeat pushes replace the workout, never duplicate. |
| `unschedule_session` | `scheduled_session_id`, `reason` | Removes from Garmin calendar, sets status `skipped` or `moved`. |
| `record_adjustment` | `plan_id`, `kind`, `reason`, `payload` | Appends to `adjustments`. |
| `add_session_note` | `text`, `tags?`, `rir_feedback?`, `scheduled_session_id?` | Appends to `session_notes`. |
| `close_plan` | `plan_id`, `outcome_summary` | Sets `completed`, records end date. |

### 11.4 Admin

| Tool | Purpose |
|---|---|
| `sync_garmin` | `full?`, `since?`. Manual sync within the time budget. |
| `map_exercise` | Map an unmapped Garmin `(category, name)` to an exercise and rebuild affected stats. |

## 12. Auth

- FastMCP `GitHubProvider` (OAuth proxy) with a GitHub OAuth App whose callback is `https://spotter-mcp.vercel.app/auth/callback`.
- Restrict access: every request checks that the GitHub `login` claim equals `ALLOWED_GITHUB_LOGIN`. Reject otherwise. Implement as middleware, not per tool.
- `client_storage`: py-key-value's `PostgreSQLStore` on the direct (unpooled) Neon URL, wrapped with `FernetEncryptionWrapper`. The store owns its table (see decisions.md).
- `jwt_signing_key` from env.

## 13. Configuration (env vars)

| Var | Purpose |
|---|---|
| `DATABASE_URL` | Neon pooled connection string |
| `DATABASE_URL_UNPOOLED` | Neon direct connection string, for migrations and OAuth client storage |
| `GARMIN_TOKEN_KEY` | Fernet key for `garmin_tokens` |
| `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET` | OAuth App |
| `ALLOWED_GITHUB_LOGIN` | The only GitHub user allowed |
| `JWT_SIGNING_KEY` | FastMCP token signing |
| `STORAGE_ENCRYPTION_KEY` | Fernet key for OAuth client storage |
| `BASE_URL` | `https://spotter-mcp.vercel.app` |

## 14. Testing

- Engine: table-driven unit tests for every rule in coaching-rules.md Part A. Target 100 % branch coverage on `spotter.engine`.
- Garmin mappers: tests against recorded fixtures in `tests/fixtures/garmin/` (anonymize before committing).
- Workout builder: snapshot test of the JSON sent to Garmin for a known prescription.
- Sync: integration tests against local Postgres with a fake Garmin client.
- MCP: in-process FastMCP client tests for each tool's input validation and output shape.
- Never hit the real Garmin API in automated tests.

## 15. Phases

Each phase is a separate Claude Code session (or a few), starts in plan mode, ends with a PR and a check of its exit criteria. Do not start a phase before the previous one's exit criteria pass.

### Phase 0: Spike (go / no-go)

Throwaway scripts in `spikes/`. Record every result in `docs/decisions.md`.

1. `bootstrap_login.py`: log in locally (handle MFA if prompted), save token JSON to a local file.
2. `read_strength.py`: fetch the last strength activity and its exercise sets. Confirm reps and weights logged in lb come back correctly and record the raw shape as a fixture.
3. `push_test_workout.py`: upload a 2-exercise strength workout with target weights (one repeat group, one set of individual steps), schedule it for tomorrow. Check on the watch: target weight shown, in lb, correct value.
4. `vercel_hello/`: deploy a FastMCP stateless server to Vercel with GitHubProvider and one tool `whoami`. Add it as a custom connector on claude.ai. Call it from the phone.
5. Add one tool to the hello server that loads the token JSON from an env var and calls `get_activities_by_date` for the last 7 days. Confirms Garmin accepts calls from Vercel IPs.

Exit: all five pass, or a documented fallback decision (e.g. move to Oracle) is in `docs/decisions.md`.

### Phase 1: Foundation

- Repo scaffold, `uv`, ruff, mypy, pytest, Docker Compose Postgres.
- Alembic migration for the full schema.
- `spotter seed-exercises` from `garminconnect.exercises`.
- `spotter.units` with tests.
- `spotter.garmin.tokens`, `spotter bootstrap-login` and `spotter import-tokens` writing encrypted tokens to Postgres; `spotter garmin-check` to verify them.
- Neon project created, migration applied to production.

Exit: `uv run pytest` green; tokens stored in Neon (imported from the Phase 0 token) and `spotter garmin-check` passes against them; `exercises` has 1,527 rows.

### Phase 2: Sync and stats

- `spotter.garmin.mappers` with fixture tests.
- `spotter.sync` with advisory lock, time budget, cursor.
- `spotter.engine.e1rm` and `spotter.sync.stats`.
- `spotter backfill --since` run locally against Neon.
- Activities of every type are synced; daily metrics move to Phase 3 (decisions.md).

Exit: production database holds full strength history with correct lb values when displayed; stats match a manual check on 3 sessions.

### Phase 3: Read-only MCP on Vercel

- FastMCP server, auth with allowlist, Postgres-backed client storage.
- Tools: `get_training_snapshot`, `get_exercise_history`, `get_readiness`, `get_endurance_load`, `get_plan`, `search_exercises`, `sync_garmin`, `map_exercise`.
- Daily metrics sync (SPEC section 9) with recorded fixtures for the section 8.2 metric calls.
- `spotter.engine.readiness` and `spotter.engine.endurance`.
- Deploy, add connector, test from phone.

Exit: from the phone, "summarize my last 4 weeks of training" returns a correct summary using only these tools.

### Phase 4: Planning and progression

- `spotter.engine.progression` and `spotter.engine.plan_eval` with full rule tests.
- Tools: `upsert_athlete_profile`, `upsert_goal`, `curate_exercise`, `create_plan`, `activate_plan`, `propose_next_session`, `evaluate_plan`, `record_adjustment`, `add_session_note`, `close_plan`.

Exit: Claude can create and activate a plan from a conversation, and `propose_next_session` returns weights that match hand-calculated expectations for 5 test scenarios.

### Phase 5: Push to Garmin

- `spotter.garmin.workouts` builder with snapshot tests.
- Tools: `push_session` (with `dry_run`), `unschedule_session`.
- Sync links completed activities to scheduled sessions.

Exit: a full loop works: propose, push, train with the watch, sync, see planned vs actual in the snapshot.

### Phase 6: Coaching rules and hardening

- Ship `src/spotter/mcp/instructions.md` from coaching-rules.md Part B.
- Set up the Claude Project with Part C.
- Clear error messages for Garmin auth expiry, 429, and partial sync.
- Structured logging (no tokens, no personal data in logs).
- Weekly `pg_dump` backup via GitHub Actions to a private location.

Exit: two weeks of real use without manual database fixes.

## 16. Open items

- Resolved in Phase 0 (see decisions.md): MFA is off on the account; the workout `description` is accepted; activities link back via `workoutId` / `metadataDTO.associatedWorkoutId`.
- Vercel function region: default `iad1` is fine; revisit only if latency is noticeable.
