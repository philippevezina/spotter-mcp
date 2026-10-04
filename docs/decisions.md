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
Status: decided, pending Phase 0 checks 4 and 5.

## 2026-10-04: python-garminconnect 0.3.17, pinned

Why: supports strength workouts with target weights, scheduling, exercise sets read and write, and the readiness metrics needed. Python, so the server is Python.
Status: decided.

## 2026-10-04: Store kg, display lb

Why: athlete thinks in lb at the gym; Garmin stores metric.
Status: decided, pending Phase 0 check 3 (round trip).

## 2026-10-04: GitHub OAuth via FastMCP, single allowed login

Why: avoids running an authorization server. Allowlist restricts access to one user.
Status: proposed, pending Phase 0 check 4.

## 2026-10-04: Project name is Spotter

Why: "Garmin Coach" is a Garmin product name. Third-party marks (Garmin, Claude) stay out of the project name. Describe compatibility in text only, e.g. "works with Garmin Connect".
Names: repo `spotter-mcp`, Python package `spotter`, CLI `spotter`, Vercel project `spotter-mcp`, Claude connector name "Spotter".
Status: decided. Check GitHub repo name availability before creating it.

## Phase 0 results

| Check | Result | Notes |
|---|---|---|
| 1. Local login (MFA?) | | |
| 2. Read strength sets, lb correct | | |
| 3. Push workout, weight shown on watch in lb | | |
| 4. FastMCP + GitHub auth on Vercel, used from phone | | |
| 5. Garmin call from Vercel IPs | | |
