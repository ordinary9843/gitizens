# World State

*Last updated: 2026-08-17*

---

## Metrics

| Field | Value |
|-------|-------|
| Era | Industrial Era |
| Laws enacted | 111 |
| Last enacted | 2026-08-17 |
| Treasury | 47,619,235 Git Coins |

### Policy

| Metric | Value |
|--------|-------|
| Education | 64/100 |
| Industry | 67/100 |
| Welfare | 58/100 |
| Green Policy | 65/100 |
| Defense | 65/100 |
| Pollution *(derived)* | 0/100 |

---

## Entities

### Institutions

| ID | Name | Built by | Trigger |
|----|------|----------|---------|
| `ins-001` | National University | [Law 005](laws/law-005.md) | education >= 55 |
| `ins-002` | Defense Ministry | [Law 018](laws/law-018.md) | defense >= 65 |

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

---

## Archive

| ID | Name | Demolished by | Reason |
|----|------|---------------|--------|
| `bld-004` | Eco-Research Center | [Law 083](laws/law-083.md) | green_policy < 75 |
| `dst-002` | Social Housing District | [Law 082](laws/law-082.md) | welfare < 48 |
| `ins-003` | Academy of Sciences | [Law 091](laws/law-091.md) | education < 70 |
| `sec-003` | Heavy Industry Zone | [Law 085](laws/law-085.md) | industry < 70 |
| `sec-004` | Smog Zone | [Law 090](laws/law-090.md) | pollution < 48 |
