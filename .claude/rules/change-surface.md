# Change Surface Checklist

Every non-trivial change to this repo touches multiple layers. Before declaring work complete, verify each affected layer below.

Use the dependency map at the bottom to know which layers apply to your change type.

---

## Layers

### 1. Code — `scripts/`
Core logic lives here. Changes to constants, timing, mechanics, or text propagate outward to every other layer.

Key files:
- `scripts/engine/constants.py` — all tunable values (`TICK_INTERVAL_HOURS`, `AI_VOTING_HOURS`, `COOLDOWN_DAYS`, etc.)
- `scripts/engine/world.py` — tick logic, era progression, `compute_next_tick_at`
- `scripts/engine/proposals.py` — voting, cooldowns, AI proposal processing
- `scripts/engine/events.py` — random event text and window strings
- `scripts/engine/chronicle.py` — narrator issue body text
- `scripts/auto_propose.py` — AI proposal body text

Check: any hardcoded numeric or string literal that duplicates a constant belongs in `constants.py`, not inline.

---

### 2. Tests — `tests/`
Expected values in tests must match constants. Timing boundary tests are especially brittle when intervals change.

Check:
```
grep -rn "hours=[0-9]\|timedelta.*hour\|assert.*00:00Z" tests/
```

When a constant changes, scan tests for hardcoded numeric expectations that mirror it.

---

### 3. Workflows — `.github/workflows/`
Cron schedules, hardcoded `GITHUB_REPOSITORY`, env vars, and permission blocks.

Check:
```
grep -rn "*/[0-9]\|ordinary9843\|GITHUB_REPOSITORY" .github/workflows/
```

`tally-votes.yml` cron must match `TICK_INTERVAL_HOURS`. The only acceptable hardcode of the repo slug is in `test.yml` (used for test isolation) — all live workflows must use `${{ github.repository }}`.

---

### 4. README — `README.md`
User-facing description of how the world works. Contains tick interval, mechanic descriptions, and badge state.

Check:
```
grep -n "hour\|tick\|2h\|every " README.md
```

---

### 5. Dashboard — `docs/index.html`
Live city UI. Contains hardcoded RAW/REPO_API URLs pointing to `ordinary9843/gitizens`, UI text describing intervals, and JS constants.

Check:
```
grep -n "ordinary9843\|2 hour\|every " docs/index.html | head -20
```

---

### 6. Skills & Commands — `.claude/skills/`, `.claude/commands/`, `.claude/rules/`
Claude's own operating instructions. If game terminology, timing, or mechanics change, any skill or rule that references them must be updated too.

Check: search for the changed term across `.claude/`.

---

### 7. Repo Description — GitHub
The one-liner shown on the repo homepage and in search results.

Update via:
```
gh repo edit ordinary9843/gitizens --description "..."
```

Check current value:
```
gh repo view ordinary9843/gitizens --json description -q .description
```

---

### 8. Repo Topics — GitHub
Tags shown on the repo page. Should reflect current era/mechanic vocabulary.

Update via:
```
gh repo edit ordinary9843/gitizens --add-topic <tag> --remove-topic <old-tag>
```

Check current topics:
```
gh repo view ordinary9843/gitizens --json repositoryTopics -q '.repositoryTopics[].topic.name'
```

---

### 9. World Data Files — `world/`
Existing law files (`world/laws/*.md`) and event output may contain inline text (timing windows, metric references) written when the old values were active. These are historical records — update only if the text is actively misleading.

Check:
```
grep -rn "2 hour\|within [0-9]" world/
```

---

### 10. CLAUDE.md
Top-level constraints and context for this repo. Update if the core loop description, tick cadence, or architectural rules change.

---

## Dependency Map

| Change type | Layers to check |
|-------------|-----------------|
| Timing / interval (tick rate, voting window) | 1, 2, 3, 4, 5, 6, 7, 9 |
| New mechanic or metric | 1, 2, 4, 5 |
| New building / structure rule | 1, 2, 5 |
| Narrator / AI text style | 1, 5, 6 |
| New workflow or CI change | 3, 6 |
| Repo rename or ownership change | 3, 5, 7, 8 |
| Era / game milestone naming | 1, 2, 4, 5, 8 |
| Constants rename or restructure | 1, 2 |
