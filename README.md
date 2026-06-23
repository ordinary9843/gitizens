<div align="center">
  <img src="docs/logo.png" alt="Gitizens" width="160" />
</div>

---

## What is Gitizens?

[![Tests](https://github.com/ordinary9843/gitizens/actions/workflows/test.yml/badge.svg)](https://github.com/ordinary9843/gitizens/actions/workflows/test.yml)
![Coverage](docs/coverage-badge.svg)

GitHub Issues are laws. Reactions are votes. Every hour, the world ticks forward on its own.

Buildings emerge when policy metrics cross thresholds. Random events strike. Eras rise and fall.  
No admin. No server. Just a repo, some GitHub Actions, and the citizens who vote.

**→ [Watch the live city on GitHub Pages](https://ordinary9843.github.io/gitizens/)**

---

## Current World Status

<!-- WORLD-STATE-START -->
![Era](https://img.shields.io/badge/Era-Modern_Era-e3b341?style=flat-square&logo=github)
![Population](https://img.shields.io/badge/Population-51524-3fb950?style=flat-square)
![Treasury](https://img.shields.io/badge/Treasury-3007_GC-388bfd?style=flat-square)
![Stability](https://img.shields.io/badge/Stability-59%2F100-bc8cff?style=flat-square)
![Pollution](https://img.shields.io/badge/Pollution-0%2F100-f85149?style=flat-square)
![Laws](https://img.shields.io/badge/Laws-25_enacted-8b949e?style=flat-square)
<!-- WORLD-STATE-END -->

<!-- STATE_START -->
**Era:** Modern Era | **Laws enacted:** 25 | [World state](world/WORLD.md)  
**Next tick:** 2026-06-23T18:00:00Z UTC
<!-- STATE_END -->

---

## Become a Citizen

1. **Star this repo** — each star earns the treasury 10 Git Coins
2. **React to open proposals** — 👍 to pass, 👎 to reject · [Open proposals](../../issues?q=label%3Aproposal+is%3Aopen)
3. **Propose a law** — open the [dashboard](https://ordinary9843.github.io/gitizens/), fill in the **PROPOSE A LAW** form, and click **Open Issue** — GitHub pre-fills the title and body for you

No signup. No account. Just a GitHub account and an opinion.

---

## How to Play

### 1. Watch the world
Open the [live city dashboard](https://ordinary9843.github.io/gitizens/). Every building reflects a real policy metric. The world ticks every hour — even when no one is online.

### 2. Vote on proposals
Open any [Issue labeled `proposal`](../../issues?q=label%3Aproposal+is%3Aopen). React with 👍 to vote for, 👎 to vote against. Voting closes in 24 hours.

### 3. Propose a law

Open the [live dashboard](https://ordinary9843.github.io/gitizens/) and scroll to **PROPOSE A LAW**. Fill in a title, choose an effect type, describe your intent, and click **Open Issue** — the form pre-fills the GitHub Issue for you. Submit it and voting starts immediately.

> **Advanced:** install [claude-gitizens](https://github.com/ordinary9843/claude-gitizens) and run `/gitizens:propose` in Claude Code for a guided, AI-assisted proposal workflow.

---

## World Mechanics

| Mechanic | How it works |
|----------|-------------|
| **Policy laws** | Change education / industry / welfare / green_policy / defense (costs 100 Git Coins) |
| **Idle growth** | World ticks every hour regardless of votes — population grows, pollution drifts, stability shifts |
| **Random events** | 15% chance per tick — drought, stock crash, alien signal, pandemic, and 47 more |
| **Era progression** | Founding → Industrial → Modern → Golden Age (or Crisis Age if things go wrong) |
| **Treasury** | Earned from GitHub stars (×10 GC) + industrial output + population tax |
| **Buildings** | Auto-created/removed by the world engine based on metric thresholds |

---
