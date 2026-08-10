# World State

*Last updated: 2026-08-10*

---

## Metrics

| Field | Value |
|-------|-------|
| Era | Crisis Age |
| Laws enacted | 88 |
| Last enacted | 2026-08-10 |
| Treasury | 59,880,556 Git Coins |

### Policy

| Metric | Value |
|--------|-------|
| Education | 75/100 |
| Industry | 65/100 |
| Welfare | 65/100 |
| Green Policy | 61/100 |
| Defense | 69/100 |
| Pollution *(derived)* | 62/100 |

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
| `dst-004` | Social Housing District | [Law 086](laws/law-086.md) | welfare >= 60 |

### Buildings

| ID | Name | Built by | Trigger |
|----|------|----------|---------|
| `bld-001` | Public School | [Law 001](laws/law-001.md) | education >= 25 |
| `bld-002` | Community Center | [Law 003](laws/law-003.md) | welfare >= 30 |
| `bld-003` | Military Barracks | [Law 008](laws/law-008.md) | defense >= 30 |

### Sectors

| ID | Name | Built by | Trigger |
|----|------|----------|---------|
| `sec-001` | Manufacturing District | [Law 007](laws/law-007.md) | industry >= 25 |
| `sec-002` | Industrial Complex | [Law 015](laws/law-015.md) | industry >= 55 |
| `sec-004` | Smog Zone | [Law 081](laws/law-081.md) | pollution >= 60 |

---

## Archive

| ID | Name | Demolished by | Reason |
|----|------|---------------|--------|
| `bld-004` | Eco-Research Center | [Law 083](laws/law-083.md) | green_policy < 75 |
| `dst-002` | Social Housing District | [Law 082](laws/law-082.md) | welfare < 48 |
| `sec-003` | Heavy Industry Zone | [Law 085](laws/law-085.md) | industry < 70 |
