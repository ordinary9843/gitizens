---
name: git
description: Enforce conventional git commit messages — single-line typed format
---

## When to use

| Situation | Action |
|-----------|--------|
| Creating a commit and need a properly formatted message | `/git` |
| Unsure which commit type prefix to use (`world`, `feat`, `fix`, etc.) | `/git` |

## Commit format

```
<type>: <short description>
```

Types: `world`, `feat`, `fix`, `docs`, `data`, `test`, `ci`, `chore`

Rules: lowercase, imperative mood, no period, under 72 characters, no multi-line body unless strictly necessary. **No `Co-Authored-By`.**

## Usage

```
/git
```

No arguments needed — the skill reads the staged diff and proposes a commit message.

---

Read `.claude/skills/git/SKILL.md` and follow the workflow exactly as written there.
