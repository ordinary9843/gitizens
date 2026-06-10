[![Gitizens](docs/banner.svg)](https://ordinary9843.github.io/gitizens/)

---

## What is Gitizens?

GitHub Issues are laws. Reactions are votes. Every 4 hours, the world ticks forward on its own.

Buildings emerge when policy metrics cross thresholds. Random events strike. Eras rise and fall.  
No admin. No server. Just a repo, some GitHub Actions, and the citizens who vote.

**→ [Watch the live city on GitHub Pages](https://ordinary9843.github.io/gitizens/)**

---

## Current World Status

<!-- WORLD-STATE-START -->
![Era](https://img.shields.io/badge/Era-Founding_Era-e3b341?style=flat-square&logo=github)
![Population](https://img.shields.io/badge/Population-1250-3fb950?style=flat-square)
![Treasury](https://img.shields.io/badge/Treasury-326_GC-388bfd?style=flat-square)
![Stability](https://img.shields.io/badge/Stability-64%2F100-bc8cff?style=flat-square)
![Pollution](https://img.shields.io/badge/Pollution-5%2F100-f85149?style=flat-square)
![Laws](https://img.shields.io/badge/Laws-8_enacted-8b949e?style=flat-square)
<!-- WORLD-STATE-END -->

[![Gitizens Dashboard](world/stats.svg)](https://ordinary9843.github.io/gitizens/)

> *Updated every 4 hours automatically · [View world history](world/WORLD.md)*

---

## Become a Citizen

1. **Star this repo** — each star earns the treasury 10 Git Coins
2. **React to open proposals** — 👍 to pass, 👎 to reject · [Open proposals](../../issues?q=label%3Aproposal+is%3Aopen)
3. **Propose a law** — install [claude-gitizens](https://github.com/ordinary9843/claude-gitizens) and run `/gitizens:propose` in Claude Code

No signup. No account. Just a GitHub account and an opinion.

---

## How to Play

### 1. Watch the world
Open the [live city dashboard](https://ordinary9843.github.io/gitizens/). Every building reflects a real policy metric. The world ticks every 4 hours — even when no one is online.

### 2. Vote on proposals
Open any [Issue labeled `proposal`](../../issues?q=label%3Aproposal+is%3Aopen). React with 👍 to vote for, 👎 to vote against. Voting closes in 24 hours.

### 3. Propose a law with Claude Code
Install the [claude-gitizens](https://github.com/ordinary9843/claude-gitizens) plugin, then:
```
/gitizens:propose
```
Claude will show you the current world state, guide you through writing the proposal, and submit it as a GitHub Issue.

---

## World Mechanics

| Mechanic | How it works |
|----------|-------------|
| **Policy laws** | Change education / industry / welfare / green_policy / defense (costs 100 Git Coins) |
| **Idle growth** | World ticks every 4h regardless of votes — population grows, pollution drifts, stability shifts |
| **Random events** | 15% chance per tick — drought, stock crash, alien signal, pandemic, and 47 more |
| **Era progression** | Founding → Industrial → Modern → Golden Age (or Crisis Age if things go wrong) |
| **Treasury** | Earned from GitHub stars (×10 GC) + industrial output + population tax |
| **Buildings** | Auto-created/removed by the world engine based on metric thresholds |

---

*World ticks every 4 hours · MIT License*
