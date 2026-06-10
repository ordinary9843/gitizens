# test — examples

Use when adding or updating test files only.

## Good

```
test: add era boundary tests for determine_era
test: cover idle economy income formula in TestIdleEconomy
test: verify event expiry handles no active event
test: add 100-entry history cap test
```

## Bad

```
test: tests                    ← too vague
test: fix test                 ← use fix: if fixing broken behavior
add tests                      ← missing type prefix
```
