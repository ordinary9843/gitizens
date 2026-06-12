---
name: Propose a Law
about: Submit a new proposal for citizens to vote on
title: "[PROPOSAL] "
labels: proposal
---

## Description

<!-- Describe what this law does and why citizens should vote for it (minimum 30 characters). -->

## Effect

```yaml
type: policy
changes:
  education: +10
```

<!--
Effect type examples:

Policy (costs 100 Git Coins):
  type: policy
  changes:
    education: +10        # valid metrics: education, industry, welfare, green_policy, defense
    industry: -5          # max ±50 per metric

Declaration (free, symbolic):
  type: declaration
  tag: my-tag

Evolve an existing entity (free):
  type: evolve
  id: bld-001
  changes:
    name: "New Name"

Direct state patch (free):
  type: state_patch
  patch:
    treasury: 200

Check the dashboard for treasury balance and cooldown status before submitting:
https://ordinary9843.github.io/gitizens/
-->
