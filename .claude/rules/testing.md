# Testing and Verification

Every feature addition or change requires all three steps before being marked complete:

1. **Unit tests** — write tests covering the new behaviour, then run `python -m pytest tests/ -q` to confirm all pass.
2. **Syntax check** — run `python -c "import ast; ast.parse(open('scripts/tally_votes.py').read()); print('OK')"` after any edit to tally_votes.py.
3. **End-to-end execution** — trigger the feature at least once on GitHub (via workflow dispatch or a real issue) and confirm it behaves correctly in the live environment.

Do not declare work complete without confirming all three steps. "Tests written" is not sufficient — they must have actually run and passed.
