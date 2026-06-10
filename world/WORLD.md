# World State

*Last updated: 2026-06-10 — [Law 008](laws/law-008.md)*

---

## Metrics

| Field | Value |
|-------|-------|
| Era | Founding Era |
| Laws enacted | 8 |
| Last enacted | 2026-06-10 |
| Treasury | 316 Git Coins |

### Policy

| Metric | Value |
|--------|-------|
| Education | 55/100 |
| Industry | 35/100 |
| Welfare | 55/100 |
| Green Policy | 50/100 |
| Defense | 35/100 |
| Pollution *(derived)* | 5/100 |

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
