# ci — examples

Use when changing CI/CD workflows or GitHub Actions only.

## Good

```
ci: run pytest on push to scripts/ or tests/
ci: trigger update-readme after tally-votes completes
ci: change tally cron to every 4 hours
ci: add SKIP_TIMING_CHECK env to test workflow
```

## Bad

```
ci: update CI                  ← too vague
ci: workflows                  ← not imperative
chore: update workflow         ← wrong type — workflow changes are `ci:`
```
