# Coaching Rules

Status: draft v1, 2026-10-04

This file has three parts:

- **Part A: Engine rules.** Implemented in `spotter.engine` as plain code, with unit tests. Deterministic.
- **Part B: Server instructions.** Shipped verbatim as `src/spotter/mcp/instructions.md`. Read by Claude when the connector starts.
- **Part C: Claude Project instructions.** Pasted into a Claude Project. Personal, editable from the app.

All thresholds below are **starting defaults to tune**, not fixed truths. Keep every threshold in one config module (`spotter.engine.rules`) so tuning never touches logic.

All loads are in **lb**.

---

## Part A: Engine rules

### A.1 Definitions

- **Working set:** a set at 60 % or more of the session's top set weight for that exercise. Lighter sets are warm-ups and are ignored.
- **Exposure:** one session in which an exercise was performed with at least one working set.
- **Hit:** every prescribed working set reached its target reps.
- **Miss:** at least one prescribed working set fell below `rep_min`.
- **RIR source:** `session_notes.rir_feedback` when present. Otherwise RIR is unknown and rules use reps only.

### A.2 Estimated 1RM

- Epley: `e1rm = weight × (1 + reps / 30)`.
- Only sets of 1 to 10 reps, only for exercises with `e1rm_eligible = true`.
- Session e1RM = best set of the session.

### A.3 Progression models

**double_progression** (default for secondary and accessory work)

1. All working sets at `rep_max` and (RIR unknown or RIR ≥ `target_rir − 1`) → add `increment_lb`, reset reps to `rep_min`. (Corrected in Phase 4: the first draft said RIR ≤ `target_rir`, which blocked easy sessions. See decisions.md.)
2. All working sets at or above `rep_min` → same weight, target +1 rep per set (capped at `rep_max`).
3. Any set below `rep_min` → same weight, same targets. Count as a miss.

**linear** (default for main lifts, early in a block)

1. Hit → add 5 lb for upper-body lifts, 10 lb for lower-body lifts (rounded to the exercise's increment).
2. Miss → same weight. Count as a miss.

**rir_based** (when RIR feedback is reliably given)

1. Reported RIR ≥ `target_rir + 2` → add 2 × increment.
2. Reported RIR = `target_rir + 1` → add 1 × increment.
3. Reported RIR within target → same weight, +1 rep if below `rep_max`.
4. Reported RIR < `target_rir - 1` → same weight; second time in a row, remove 1 × increment.
5. RIR missing → fall back to double_progression for that exposure.

### A.4 Stalls and resets

- 2 misses in a row on the same exercise → **hold** (same prescription), flag `stall_warning`.
- 3 misses in a row → **reset**: reduce load 10 % (rounded down to the increment) and restart at `rep_min`. Flag `stall` so Claude can choose a variation swap instead.
- A reset counts toward `end_criteria.stall_lifts` only for exercises with role `main`.

### A.5 Readiness classification (per day)

| Class | Any of |
|---|---|
| red | HRV status LOW or POOR; training readiness < 25; sleep score < 50 and Body Battery max < 40 |
| amber | HRV below baseline low; training readiness 25 to 49; sleep score < 60 |
| green | none of the above |

Missing data never produces red on its own. Report it as `unknown` in reasons.

### A.6 Readiness modifiers (applied in `propose_next_session`)

- **green:** progression as computed.
- **amber:** no load increase. Keep last weight and reps.
- **red:** no load increase, add 1 to `target_rir`, remove 1 set from main and secondary lifts (minimum 2 sets). Suggest moving the session if the previous day was also red.

### A.7 Endurance interference

Flag a planned lower-body day (any `main` exercise with pattern squat, hinge or lunge) when, in the 48 h before it:

- a run of 75 min or more, or with aerobic training effect ≥ 4.0, or anaerobic training effect ≥ 3.0; or
- a ride of 120 min or more, or with aerobic training effect ≥ 4.0.

Effect: lower-body main lifts get no load increase. Upper-body work is unaffected. Claude may reorder the week instead.

### A.8 Volume guardrails (checked in `create_plan`)

- Weekly hard sets per primary muscle: warn below 8 or above 20.
- New plan with no comparable recent history: start at 10 to 12 sets per muscle per week.
- Working sets per session: warn above 25.
- Count a set toward a muscle as 1 if primary, 0.5 if secondary.

### A.9 Deload

- In the deload week: sets halved (rounded up), load −10 % (rounded to increment), `target_rir` +2, no progression.
- An unplanned deload can be triggered by Claude via `record_adjustment(kind="deload")` when 2 or more main lifts are flagged `stall_warning` in the same week.

### A.10 Plan end criteria

Default `end_criteria`:

```json
{ "max_weeks": 6, "stall_lifts": 2, "min_adherence": 0.7, "adherence_window_weeks": 2 }
```

`evaluate_plan` returns:

- `end` when the planned weeks are done, or `stall_lifts` main lifts have reset in this plan.
- `adjust` when adherence over the last `adherence_window_weeks` is below `min_adherence`.
- `continue` otherwise.

Blocks should be 4 to 8 weeks. Claude picks the length when creating the plan and explains why in `rationale`.

### A.11 Rounding and units

- Round every proposed load to the exercise's `increment_lb`. Barbell loads never go below 45 lb.
- Dumbbell loads are per hand.
- Storage and Garmin use kg; conversion happens only in `spotter.units`.

### A.12 First exposure

No history for an exercise in the last 12 weeks → no computed load. `propose_next_session` returns `needs_calibration` for that exercise. Claude asks for a recent working weight or prescribes a conservative first session at RIR 3.

---

## Part B: Server instructions (ship as `src/spotter/mcp/instructions.md`)

```
You are a strength coach for one athlete. Garmin data is the record of what was done.
This server is the record of what was planned and why.

Protocol
- Start every conversation with get_training_snapshot.
- Loads are in lb. Never convert to kg in replies.
- Use propose_next_session before suggesting weights. Treat its output as the default.
  You may override it with a reason. Log every override with record_adjustment.
- Never call push_session without explicit confirmation from the athlete in this
  conversation. Use dry_run first when the session differs from the proposal.
- If an exercise returns needs_calibration, ask for a recent working weight.
- After a completed session, ask how hard the main lifts felt (reps in reserve)
  and save it with add_session_note.

Judgement
- Readiness and endurance flags are inputs, not orders. Explain your choice in one line.
- Prefer moving a session over gutting it when two red days stack up.
- When evaluate_plan says end, propose the next block. Summarize what worked first.

Style
- Short answers. A session is a compact table: exercise, sets x reps, load, rest.
```

---

## Part C: Claude Project instructions (template)

```
Context
- Goals: <primary goal and target date>
- Secondary: <e.g. half marathon on YYYY-MM-DD, keep running 3x/week>
- Schedule: strength on <days>, runs on <days>, long run on <day>
- Session length: <minutes>
- Gym: <home/commercial>, smallest plates <2.5 lb>, dumbbells to <lb>

Preferences
- Exercises I like: <...>
- Exercises to avoid: <...>
- Limitations to work around: <...>

How I want to work
- Morning check: "What's today?" means snapshot, proposal, then wait for my OK to push.
- Weekly review on <day>: evaluate_plan and a short summary.
```
