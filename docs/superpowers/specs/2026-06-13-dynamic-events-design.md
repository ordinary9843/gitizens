# Dynamic Event Generation via LLM

**Date:** 2026-06-13  
**Status:** Approved  
**Goal:** Replace the fixed event pool with LLM-generated events that emerge from the current world state, making the simulation unpredictable even to its creator.

---

## Problem

The existing `world/event_pool.json` contains 53 hand-authored events. Once a player has seen all events, the world becomes predictable. The fixed pool cannot react to the specific combination of laws, history, and metrics that the world has accumulated — every event feels generic rather than earned.

---

## Solution

Replace `fire_random_event()` with a call to `gpt-4o-mini` (GitHub Models API, free tier) that generates a contextually appropriate event from scratch. The fixed pool is retained only as a fallback when the LLM fails.

---

## Architecture

### New module: `scripts/engine/event_generator.py`

Owns all LLM generation logic. `events.py` imports and calls it; no other files change.

```
event_generator.py
├── generate_event(state, laws, recent_events, world_summary) -> dict | None
│     ├── build_prompt(...)
│     ├── call LLM (client.chat.completions.create)
│     ├── parse_llm_output(raw) -> dict | None
│     ├── validate_event(event) -> bool
│     ├── apply_clamps(event, state) -> dict
│     └── on failure: return None
│
└── generate_chained_event(resolved_event, responded, state, ...) -> dict | None
      └── same pipeline, different prompt context
```

### Changes to `events.py`

- `fire_random_event(state)`: call `generate_event()`; on `None` result, call existing pool logic (extracted to `_fallback_from_pool(state)`)
- `fire_chained_event(resolved, responded)`: call `generate_chained_event()`; on `None`, silently skip (no chain)
- All other functions (`open_event_issue`, `close_event_issue`, `check_event_expiry`, `apply_crisis_multiplier`) unchanged

---

## Prompt Design

### System prompt (static)

```
You are a world event generator for a GitHub-native nation simulation.
Generate a single world event as a JSON object. The event must feel like a
natural consequence of the current world state. Extreme events (war, famine,
revolution, collapse) are allowed and encouraged when the state warrants it.
The world must always have a path to recovery — no single event may push
all metrics below 5 simultaneously.
```

### User prompt (dynamic)

Includes:
- Current era, tick count, laws count
- All numeric metrics (treasury, education, industry, welfare, green_policy, defense, pollution, stability)
- Up to 10 most recent active law titles (from `world/laws_index.json`, sorted by `enacted_at` desc)
- Last 5 event titles with their outcomes (responded / no response)
- World trend: `improving` / `stable` / `deteriorating` (derived from `world/history.json`: compare average of key metrics across last 3 snapshots vs previous 3; delta > +5 = improving, < -5 = deteriorating, else stable)
- `world_summary` string from state

Required output schema:
```json
{
  "id": "evt-llm-<slug>",
  "category": "<natural|economic|health|security|scientific|social|political|cosmic|weird>",
  "rarity": "<common|uncommon|rare|legendary>",
  "title": "<short dramatic title>",
  "description": "<2-3 sentences>",
  "flavor": "<1 sentence atmospheric detail>",
  "immediate_effects": {"<metric>": <int>},
  "response_consequence": {"<metric>": <int>},
  "default_consequence": {"<metric>": <int>},
  "response_hint": "<what law would help>",
  "duration_hours": 4,
  "chained_from": null
}
```

Rarity magnitude guide in prompt:
- `common`: ±3–10 per metric, 1–2 metrics affected
- `uncommon`: ±8–20 per metric, 2–3 metrics affected
- `rare`: ±15–35 per metric, 3–4 metrics affected
- `legendary`: ±25–60 per metric, 4+ metrics, duration 24–72h

### Chain event prompt addition

Appended to user prompt when generating a follow-up:
```
The previous event "<title>" just ended.
Citizens <responded / did not respond to> the event.
The consequence applied was: <consequence dict>.
Generate the natural follow-up event that emerges from this outcome.
Set "chained_from" to "<previous event id>".
```

---

## Validation Pipeline

Three layers applied in order:

### Layer 1: JSON parse
- Try `json.loads(raw)`
- If fails, extract from ` ```json ... ``` ` code block with regex
- If still fails: return `None` → fallback

### Layer 2: Schema validation
Required fields: `id`, `category`, `rarity`, `title`, `description`, `immediate_effects`, `response_consequence`, `default_consequence`, `duration_hours`

Whitelist checks:
- `rarity` must be in `{common, uncommon, rare, legendary}`
- `category` must be in `{natural, economic, health, security, scientific, social, political, cosmic, weird}`
- All effect dicts must be `dict[str, int|float]` (no nested objects, no strings)
- `duration_hours` must be a positive number; if missing or invalid, default to `4`

Any failure: return `None` → fallback

### Layer 3: Hard clamps (correct, do not reject)
```python
def apply_clamps(event, current_state):
    for key in ("immediate_effects", "response_consequence", "default_consequence"):
        for metric, delta in list(event.get(key, {}).items()):
            # Cap single-event swing at ±50
            delta = max(-50, min(50, int(round(delta))))
            # World survival guarantee: no metric below 5
            current = current_state.get(metric, 50)
            if current + delta < 5:
                delta = max(5 - current, -50)
            event[key][metric] = delta
    return event
```

---

## Fallback Strategy

```
LLM call raises exception          → _fallback_from_pool(state)
LLM returns empty / unparseable    → _fallback_from_pool(state)
Schema validation fails            → _fallback_from_pool(state)
Pool is empty                      → return None (no event this tick, safe)
Chain event LLM fails              → silently skip, no chain fires
```

Log line on fallback: `[EVENT] LLM generation failed, using pool fallback`

---

## Test Plan (`tests/test_event_generator.py`)

New file. Does not modify existing `tests/test_events.py`.

### Group 1: JSON parsing
- Valid JSON string → parsed correctly
- JSON wrapped in ` ```json ``` ` block → extracted and parsed
- Partial JSON (truncated) → returns `None`
- Pure prose (no JSON) → returns `None`
- Empty string → returns `None`
- Valid JSON but wrong type (list instead of dict) → returns `None`

### Group 2: Schema validation
- All required fields present → passes
- Missing `title` → returns `None`
- Missing `immediate_effects` → returns `None`
- `rarity = "mythic"` (not in whitelist) → returns `None`
- `category = "military"` (not in whitelist) → returns `None`
- `immediate_effects` contains a string value → returns `None`
- `immediate_effects` is a list → returns `None`
- `duration_hours` is `"four"` (string) → defaults to `4`, does not reject

### Group 3: Hard clamps
- `delta = +80` → clamped to `+50`
- `delta = -80` → clamped to `-50`
- `delta = -3` with `current_metric = 4` → clamped to `-(-1)` i.e. result = 5
- `delta = 0` → unchanged
- `current_metric = 0`, `delta = -10` → delta becomes 0 (already at floor)
- Multiple metrics all large negative → each independently clamped, none result < 5
- `delta = 50` exactly → allowed (boundary)
- `delta = 51` → clamped to 50

### Group 4: Fallback routing
- LLM client raises `Exception` → `_fallback_from_pool` called, returns event from pool
- LLM returns `""` → fallback called
- LLM returns `"{}"` (empty dict, missing fields) → fallback called
- Pool is empty list → `fire_random_event` returns `None`, no crash
- Fallback print message contains `"LLM generation failed"`

### Group 5: Chain events
- `generate_chained_event(resolved, responded=True, ...)` → prompt contains `"responded"`
- `generate_chained_event(resolved, responded=False, ...)` → prompt contains `"did not respond"`
- Generated chain event has `chained_from` set to resolved event's `id`
- LLM fails for chain → function returns `None`, `fire_chained_event` silently skips
- Active event already exists when chain would fire → chain skipped, no overwrite

### Group 6: Trigger probability
- `random.random()` mocked to `0.14` → event generation attempted
- `random.random()` mocked to `0.16` → returns `None` immediately, no LLM call
- `random.random()` mocked to `0.0` → generation attempted (boundary)
- `random.random()` mocked to `0.15` exactly → generation attempted (0.15 > 0.15 is False, so continues)

### Group 7: Integration (end-to-end with mocked LLM)
- Valid LLM output → event stored in `active_event.json` via `save_active_event`
- LLM output with oversized effects → clamps applied before storage
- `check_event_expiry` on LLM-generated event → resolves correctly, chain generation triggered
- Full tick simulation: `fire_random_event` → `open_event_issue` → `check_event_expiry` → `close_event_issue`

---

## Files Changed

| File | Change |
|------|--------|
| `scripts/engine/event_generator.py` | New — all LLM generation logic |
| `scripts/engine/events.py` | `fire_random_event` + `fire_chained_event` updated |
| `scripts/engine/__init__.py` | Export new public symbols if needed |
| `tests/test_event_generator.py` | New — full test suite |
| `world/event_pool.json` | Unchanged (retained as fallback) |

---

## What Does Not Change

- `open_event_issue()` — unchanged
- `close_event_issue()` — unchanged
- `check_event_expiry()` — unchanged
- `apply_crisis_multiplier()` — unchanged
- `world/event_pool.json` — retained as fallback pool
- `world/active_event.json` schema — unchanged
- All existing tests in `tests/test_events.py` — must continue to pass
