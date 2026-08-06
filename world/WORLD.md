# World State

*Last updated: 2026-08-06 — [Law 082](laws/law-082.md)*

---

## Metrics

| Field | Value |
|-------|-------|
| Era | Crisis Age |
| Laws enacted | 82 |
| Last enacted | 2026-08-06 |
| Treasury | 60,592,903 Git Coins |

### Policy

| Metric | Value |
|--------|-------|
| Education | 83/100 |
| Industry | 83/100 |
| Welfare | 43/100 |
| Green Policy | 80/100 |
| Defense | 89/100 |
| Pollution *(derived)* | 100/100 |

---

## Entities

### Institutions

| ID | Name | Built by | Trigger |
|----|------|----------|---------|
| `ins-001` | National University | [Law 005](laws/law-005.md) | education >= 55 |
| `ins-002` | Defense Ministry | [Law 018](laws/law-018.md) | defense >= 65 |
| `ins-003` | Academy of Sciences | [Law 021](laws/law-021.md) | education >= 80 |

### Districts

| ID | Name | Built by | Trigger |
|----|------|----------|---------|
| `dst-001` | City Park | [Law 006](laws/law-006.md) | green_policy >= 35 |
| `dst-003` | Nature Reserve | — | green_policy >= 65 |

### Buildings

| ID | Name | Built by | Trigger |
|----|------|----------|---------|
| `bld-001` | Public School | [Law 001](laws/law-001.md) | education >= 25 |
| `bld-002` | Community Center | [Law 003](laws/law-003.md) | welfare >= 30 |
| `bld-003` | Military Barracks | [Law 008](laws/law-008.md) | defense >= 30 |
| `bld-004` | Eco-Research Center | [Law 023](laws/law-023.md) | green_policy >= 85 |

### Sectors

| ID | Name | Built by | Trigger |
|----|------|----------|---------|
| `sec-001` | Manufacturing District | [Law 007](laws/law-007.md) | industry >= 25 |
| `sec-002` | Industrial Complex | [Law 015](laws/law-015.md) | industry >= 55 |
| `sec-003` | Heavy Industry Zone | — | industry >= 80 |
| `sec-004` | Smog Zone | [Law 081](laws/law-081.md) | pollution >= 60 |

---

## Archive

| ID | Name | Demolished by | Reason |
|----|------|---------------|--------|
| `dst-002` | Social Housing District | [Law 082](laws/law-082.md) | welfare < 48 |
