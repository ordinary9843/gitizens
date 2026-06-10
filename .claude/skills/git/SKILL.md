---
name: git
description: "Enforce conventional git commit messages. Every change gets a single-line commit in the format <type>: <short description>. Types: world, feat, fix, docs, data, test, ci, chore. No Co-Authored-By. No trailers."
---

# Git Commit Convention

> **Goal**: Every commit must have a short, single-line message in the format `<type>: <description>`. The description should be lowercase, imperative, and under 72 characters. No multi-line bodies unless the change genuinely cannot be understood without one.

## Format

```
<type>: <short description>
```

- **type**: one of the allowed prefixes (see below)
- **description**: imperative mood, lowercase, no period at end
- **length**: aim for under 50 chars, hard limit 72 chars

## Allowed Types

| Type | When to use |
|---|---|
| `world` | In-game world changes (tick, law enacted, event fired, era transition) |
| `feat` | Adding new functionality |
| `fix` | Fixing a bug or broken behavior |
| `docs` | Changes to documentation or markdown files only |
| `data` | Static data files (event_pool, seed data, world templates) |
| `test` | Adding or updating tests |
| `ci` | CI/CD pipeline or workflow changes |
| `chore` | Maintenance tasks, config changes, cleanup |

## Rules

1. One commit per logical change — do not bundle unrelated changes
2. Single line only — no body, no footer unless strictly necessary
3. Lowercase description — `feat: add proposal validator` not `feat: Add Proposal Validator`
4. Imperative mood — `fix: clamp pollution delta` not `fixed` or `fixes`
5. No vague messages — `chore: update` is bad, `chore: remove dev planning docs` is good
6. **No `Co-Authored-By`** — never add this trailer
7. **No force push** to master

## When a Body IS Acceptable

Only when the "why" cannot be inferred from the type + description alone. Keep it to 1–2 sentences. Leave one blank line between subject and body.

```
fix: cap population growth when welfare exceeds 80

Uncapped growth caused treasury overflow after 10+ enacted welfare
laws in a single session — added clamp in world_autonomous_tick().
```

## Examples

See `examples/` for good and bad examples per type:
- examples/world.md
- examples/feat.md
- examples/fix.md
- examples/docs.md
- examples/data.md
- examples/test.md
- examples/ci.md
- examples/chore.md
