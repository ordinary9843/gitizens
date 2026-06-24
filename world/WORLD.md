# World State

*Last updated: 2026-06-24*

---

## Metrics

| Field | Value |
|-------|-------|
| Era | Modern Era |
| Laws enacted | 30 |
| Last enacted | 2026-06-24 |
| Treasury | 30 Git Coins |

### Policy

| Metric | Value |
|--------|-------|
| Education | 97/100 |
| Industry | 98/100 |
| Welfare | 100/100 |
| Green Policy | 100/100 |
| Defense | 94/100 |
| Pollution *(derived)* | 0/100 |

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
| `dst-002` | Social Housing District | [Law 010](laws/law-010.md) | welfare >= 60 |
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

---

## Archive

*(none)*
