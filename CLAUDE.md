# Gitizens

GitHub-native civilization simulator. Issues are proposals, reactions are votes, a cron workflow tallies votes every hour and updates world state.

## Change surface

Before completing any non-trivial change, verify all affected layers — see `.claude/rules/change-surface.md` for the full checklist and dependency map.

## Core constraints

- `world/` files are modified only via `scripts/tally_votes.py` or GitHub Actions — never edit manually
- Never force-push master
- Never commit `scripts/__pycache__/` or `*.pyc` files
- After any local test run: `git checkout world/state.json`

## GitHub configuration (do not change)

- master branch protection must NOT have `required_status_checks` — the tally bot pushes directly and cannot satisfy a status check requirement without creating a deadlock
- `tally-votes.yml` permissions must include `models: read` for GitHub Models API (narrator) and `contents: write` for pushing world state
