# world — examples

Use when the world engine changes game state: laws enacted, events fired, autonomous tick, era transitions.

## Good

```
world: [law-001] raise education tax enacted (#12)
world: proposal rejected — cut defense spending (#15)
world: autonomous tick
world: event resolved: great harvest
world: treasury +120 from 12 star(s)
```

## Bad

```
world: update          ← too vague
world: Law enacted     ← not lowercase, no law number
Update world state     ← missing type prefix
world: enacted law 5 about raising taxes in the sector of education  ← too long
```

## Notes

- Always include law number and issue number when a law is enacted: `[law-NNN] title (#{issue})`
- Event commits include the event title for searchability
- Autonomous tick commits are always exactly `world: autonomous tick`
