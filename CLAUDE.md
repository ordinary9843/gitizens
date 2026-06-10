# Gitizens

## Project rules

- Never modify world/ files manually outside of tests — all changes go through `scripts/tally_votes.py` or GitHub Issues
- Never push directly to master with force
- Always run syntax check after editing tally_votes.py: `python -c "import ast; ast.parse(open('scripts/tally_votes.py').read()); print('OK')"`
- Run tests before committing engine changes: `python -m pytest tests/ -q`
- Never commit `scripts/__pycache__/` or `*.pyc` files
- Revert world/state.json after local test runs: `git checkout world/state.json`
