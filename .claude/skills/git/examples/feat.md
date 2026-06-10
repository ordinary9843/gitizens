# feat — examples

Use when adding new functionality that didn't exist before.

## Good

```
feat: add history chart to landing page
feat: support event response detection in tally
feat: add era progression display to world page
feat: show active event banner in status skill
```

## Bad

```
feat: added new stuff          ← too vague
feat: Feature                  ← not imperative, not lowercase
feat: implement the entire new event system that fires random events every 4 hours based on world conditions  ← too long
new feature                    ← missing type prefix
```

## Borderline Cases

If the change is wiring up an existing tool rather than exposing new behavior, `chore` may be more accurate.

```
chore: wire up event_pool.json to tally_votes loader   ← setup only
feat: fire random events every 4h based on world state  ← behavior now active
```
