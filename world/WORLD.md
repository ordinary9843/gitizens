# World State

*Last updated: 2026-06-17*

---

## Metrics

| Field | Value |
|-------|-------|
| Era | Industrial Era |
| Laws enacted | 17 |
| Last enacted | 2026-06-17 |
| Treasury | 171 Git Coins |

### Policy

| Metric | Value |
|--------|-------|
| Education | 72/100 |
| Industry | 63/100 |
| Welfare | 93/100 |
| Green Policy | 73/100 |
| Defense | 59/100 |
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
| `sec-002` | Industrial Complex | [Law 015](laws/law-015.md) | industry >= 55 |

---

## Archive

*(none)*
