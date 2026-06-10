# Constitution of Gitizens

*Ratified at founding. Amendable by supermajority.*

---

## Article I — The Nation

Gitizens is a nation that exists entirely within this GitHub repository.
The git history is the permanent, irrevocable record of all laws and events.

## Article II — Citizenship

Any GitHub user is a citizen. No registration required. One account, one vote.

## Article III — Legislation

### Proposals

Any citizen may propose a law by using `/gitizens:propose` in Claude Code.
The proposal is submitted as a GitHub Issue with title starting with `[PROPOSAL]` and passes automated format validation before a `proposal` label is applied.

### Voting

- Voting opens immediately when the `proposal` label is applied
- Voting period: **24 hours** from Issue creation
- Vote FOR: 👍 reaction on the Issue
- Vote AGAINST: 👎 reaction on the Issue
- Each account's most recent reaction counts; earlier reactions for the same account are ignored

### Passage

A proposal becomes law when, after 24 hours:

- More FOR votes than AGAINST votes were cast

**Special cases:**
- Zero total votes → closed silently, not recorded in history
- Tied (FOR == AGAINST) → rejected

### Policy Proposals

Laws of type `policy` change the five policy metrics (education / industry / welfare / green_policy / defense) and cost **100 Git Coins** from the treasury. A proposal is blocked at tally time if the treasury is insufficient.

### Constitutional Amendments

Amendments to this document require a proposal labeled `constitutional` with:

1. At least **5 votes** cast, AND
2. At least **66%** of votes in favor

## Article IV — Records

All enacted laws are stored in `world/laws/` as individual files. Each file records the proposer, all voters (for and against), the vote count, and the effect applied.

All proposals (passed and rejected) are recorded in `history/INDEX.md`.

The git commit history is the ground truth — no file can override what is in the log.

## Article V — World Mechanics

The world advances automatically every **4 hours** via GitHub Actions. Each cycle:

1. **Idle tick** — population grows, pollution drifts, stability shifts, treasury earns idle income
2. **Vote tally** — open proposals past the 24h window are tallied and laws enacted or rejected
3. **Random event** — 15% chance per cycle; a random event from the event pool fires, opening a GitHub Issue with a 4-hour response window
4. **Era progression** — the world's era is recomputed from current metrics after every change:
   - Founding Era (default)
   - Industrial Era (industry > 60, education > 50)
   - Modern Era (all policy metrics > 65)
   - Golden Age (all policy metrics > 80, stability > 80)
   - Crisis Age (pollution > 75 OR stability < 25)

The bot commits under the `gitizens-bot` identity with the prefix `world:`. No human moderator is required.

## Article VI — Treasury

The treasury is measured in **Git Coins (GC)**. Income sources:

- GitHub ⭐ stars: ×10 GC each (counted once per star)
- Industrial output: `floor(industry / 10)` GC per tick
- Population tax: `floor(population / 500)` GC per tick
