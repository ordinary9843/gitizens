# World State

*Last updated: 2026-06-11*

---

## Metrics

| Field | Value |
|-------|-------|
| Era | Founding Era |
| Laws enacted | 12 |
| Last enacted | 2026-06-11 |
| Treasury | 67 Git Coins |

### Policy

| Metric | Value |
|--------|-------|
| Education | 66/100 |
| Industry | 45/100 |
| Welfare | 68/100 |
| Green Policy | 67/100 |
| Defense | 41/100 |
| Pollution *(derived)* | 0/100 |

---

## Entities

### Institutions

| ID | Name | Built by | Trigger |
|----|------|----------|---------|
| `ins-001` | National University | [Law 005](laws/law-005.md) | education >= 55 |

### Districts

| ID | Name | Built by | Trigger |
|----|------|----------|---------|
| `dst-001` | City Park | [Law 006](laws/law-006.md) | green_policy >= 35 |
| `dst-002` | Social Housing District | [Law 010](laws/law-010.md) | welfare >= 60 |
| `dst-003` | Nature Reserve | — | green_policy >= 65 |

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

---

## Archive

*(none)*
