# Gitizens

GitHub-native civilization simulator. Issues are proposals, reactions are votes, a cron workflow tallies votes every 2 hours and updates world state.

## Core constraints

- `world/` files are modified only via `scripts/tally_votes.py` or GitHub Actions — never edit manually
- Never force-push master
- Never commit `scripts/__pycache__/` or `*.pyc` files
- After any local test run: `git checkout world/state.json`
