# data — examples

Use when changing static data files: event pool, seed world state, entity templates.

## Good

```
data: add 5 rare cosmic events to event_pool
data: seed initial world state with founding era values
data: add building templates for industrial district
data: expand event pool to 51 entries across 9 categories
```

## Bad

```
data: update data files        ← too vague
data: events                   ← not imperative
chore: add events              ← wrong type — data changes are `data:`
```
