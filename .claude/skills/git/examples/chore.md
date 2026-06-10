# chore — examples

Use for maintenance tasks, config changes, and cleanup with no behavior change.

## Good

```
chore: remove dev planning docs from docs/
chore: add pytest_cache to .gitignore
chore: rename state field green_policy to green
chore: move scripts/_event_body.txt to .gitignore
```

## Bad

```
chore: update stuff            ← too vague
chore: changes                 ← not imperative
cleanup                        ← missing type prefix
chore: update node to v20      ← wrong project (this is Python)
```
