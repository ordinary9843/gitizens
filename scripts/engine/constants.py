VOTING_PERIOD_DAYS = 1
AI_VOTING_HOURS = 2
SIGNATURE_THRESHOLD = 10
COOLDOWN_DAYS = 3
ANNALS_INTERVAL = 10
REPRESENTATIVE_DAYS = 7

CATEGORIES = [
    ("institutions", "Institutions"),
    ("districts",    "Districts"),
    ("buildings",    "Buildings"),
    ("sectors",      "Sectors"),
]

CATEGORY_COLORS = {
    "institutions": "#388bfd",
    "buildings":    "#e3b341",
    "districts":    "#3fb950",
    "sectors":      "#bc8cff",
}

POLICY_METRICS = {"education", "industry", "welfare", "green_policy", "defense"}
POLICY_COST = 100

BASE_STATE_FIELDS = {
    "era", "laws_count", "last_enacted", "world_summary", "founded_date",
    "treasury", "currency", "stars_last_counted", "known_stargazers",
    "education", "industry", "welfare", "green_policy", "defense",
    "population", "pollution", "stability",
    "tags_applied", "next_tick_at", "last_narrator_date",
}

_EVOLVE_BLOCKED = {
    "id", "built_law", "built_at", "auto_trigger",
    "demolished_law", "demolished_at", "demolished_reason", "last_evolved_law",
}

WORLD_GENERATION_RULES = [
    ("education",    25, "buildings",    "Public School",             20),
    ("education",    55, "institutions", "National University",       45),
    ("education",    80, "institutions", "Academy of Sciences",       70),
    ("industry",     25, "sectors",      "Manufacturing District",    20),
    ("industry",     55, "sectors",      "Industrial Complex",        45),
    ("industry",     80, "sectors",      "Heavy Industry Zone",       70),
    ("welfare",      30, "buildings",    "Community Center",          22),
    ("welfare",      60, "districts",    "Social Housing District",   48),
    ("green_policy", 35, "districts",    "City Park",                 28),
    ("green_policy", 65, "districts",    "Nature Reserve",            52),
    ("green_policy", 85, "buildings",    "Eco-Research Center",       75),
    ("defense",      30, "buildings",    "Military Barracks",         22),
    ("defense",      65, "institutions", "Defense Ministry",          55),
    ("pollution",    60, "sectors",      "Smog Zone",                 48),
]

THRESHOLD_TAGS = [
    ("education",    "above", 50, "milestone/educated-society"),
    ("industry",     "above", 50, "milestone/industrial-age"),
    ("green_policy", "above", 60, "milestone/green-era"),
    ("defense",      "above", 50, "milestone/militarized-state"),
    ("welfare",      "above", 60, "milestone/welfare-state"),
    ("pollution",    "above", 60, "crisis/pollution-crisis"),
    ("pollution",    "below", 20, "recovery/air-cleaned"),
    ("population",   "above", 2000, "milestone/population-boom"),
]

RARITY_WEIGHTS = {"common": 60, "uncommon": 25, "rare": 10, "legendary": 5}

CATEGORY_MULTIPLIERS: dict[str, list[tuple[str, str, int | float, float]]] = {
    "natural":    [("green_policy", "low",  40, 2.0), ("green_policy", "high", 70, 0.6)],
    "economic":   [("industry",     "high", 60, 1.5), ("treasury",     "low",  50, 1.4)],
    "health":     [("welfare",      "low",  35, 2.0), ("welfare",      "high", 65, 0.6)],
    "security":   [("defense",      "low",  35, 2.0)],
    "scientific": [("education",    "high", 65, 1.5)],
    "social":     [("welfare",      "low",  40, 1.5), ("stability",    "low",  40, 1.5)],
}
