#!/usr/bin/env python3
"""
Tally votes on all open proposal Issues and apply effects.
Called by tally-votes.yml every 6 hours.
"""
import os
import json
import math
import re
import random
import subprocess
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from openai import OpenAI

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
VOTING_PERIOD_DAYS = 1
AI_VOTING_HOURS = 4
SIGNATURE_THRESHOLD = 10
COOLDOWN_DAYS = 14
ANNALS_INTERVAL = 10
REPRESENTATIVE_DAYS = 7
SKIP_TIMING = os.environ.get("SKIP_TIMING_CHECK", "").lower() in ("1", "true", "yes")

client = OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GITHUB_TOKEN,
)

# ── Constants ────────────────────────────────────────────────────────────────

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
POLICY_COST = 100  # Git Coins per policy proposal

BASE_STATE_FIELDS = {
    "era", "laws_count", "last_enacted", "world_summary", "founded_date",
    "treasury", "currency", "stars_last_counted", "known_stargazers",
    "education", "industry", "welfare", "green_policy", "defense",
    "population", "pollution", "stability",
    "tags_applied", "next_tick_at", "last_narrator_date",
}

# System-managed entity fields that proposals cannot overwrite
_EVOLVE_BLOCKED = {
    "id", "built_law", "built_at", "auto_trigger",
    "demolished_law", "demolished_at", "demolished_reason", "last_evolved_law",
}

# (metric, appear_threshold, category, entity_name, remove_threshold)
# Hysteresis gap prevents oscillation at the boundary
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
    # Pollution-triggered: city degradation when industrial waste accumulates
    ("pollution",    60, "sectors",      "Smog Zone",                 48),
]

# (field, direction, threshold, tag_name) — fires only on first crossing
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


# ── I/O helpers ──────────────────────────────────────────────────────────────

def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 and result.stderr.strip():
        print(f"  [WARN] {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def gh_json(cmd: list[str]) -> list | dict:
    out = run(["gh", *cmd])
    return json.loads(out) if out else []


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_state() -> dict:
    return read_json(Path("world/state.json"))


def write_state(state: dict):
    write_json(Path("world/state.json"), state)


def read_stats() -> dict:
    path = Path("world/stats.json")
    if not path.exists():
        return {"proposals_total": 0, "proposals_passed": 0,
                "proposals_rejected": 0, "proposals_silent": 0}
    return read_json(path)


def write_stats(stats: dict):
    write_json(Path("world/stats.json"), stats)


def load_event_pool() -> list:
    path = Path("world/event_pool.json")
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_active_event() -> dict:
    path = Path("world/active_event.json")
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_active_event(event: dict):
    Path("world/active_event.json").write_text(
        json.dumps(event, indent=2) + "\n", encoding="utf-8"
    )


def append_history_snapshot(state: dict):
    hist_path = Path("world/history.json")
    try:
        history = json.loads(hist_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        history = []
    active_id = load_active_event().get("id")
    snapshot = {
        "tick":         (history[-1]["tick"] + 1) if history else 1,
        "date":         datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "laws_count":   state.get("laws_count", 0),
        "era":          state.get("era", "Founding Era"),
        "education":    state.get("education", 0),
        "industry":     state.get("industry", 0),
        "welfare":      state.get("welfare", 0),
        "green_policy": state.get("green_policy", 0),
        "defense":      state.get("defense", 0),
        "pollution":    state.get("pollution", 0),
        "population":   state.get("population", 1000),
        "stability":    state.get("stability", 79),
        "treasury":     state.get("treasury", 0),
        "active_event": active_id,
    }
    history.append(snapshot)
    if len(history) > 100:
        history = history[-100:]
    hist_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")


def apply_event_effects(event: dict, consequence_key: str):
    state = read_state()
    effects = event.get(consequence_key, {})
    for field, delta in effects.items():
        if field == "all_random":
            for m in list(POLICY_METRICS) + ["stability", "pollution"]:
                state[m] = max(0, min(100, state.get(m, 0) + random.randint(-10, 10)))
        elif field in POLICY_METRICS:
            state[field] = max(0, min(100, state.get(field, 0) + int(delta)))
        elif field in ("treasury", "population"):
            state[field] = max(0, state.get(field, 0) + int(delta))
        elif field in ("stability", "pollution"):
            state[field] = max(0, min(100, state.get(field, 0) + int(delta)))
    write_state(state)


def fire_random_event(state: dict) -> dict | None:
    if random.random() > 0.15:
        return None
    pool = load_event_pool()
    if not pool:
        return None
    edu = state.get("education", 0)
    edu_bonus = 5 if edu > 70 else 0
    eligible = []
    for event in pool:
        conds = event.get("trigger_conditions", {})
        ok = all(
            (state.get(f, 0) >= r.get("min", 0) and state.get(f, 0) <= r.get("max", 999))
            for f, r in conds.items()
        )
        if ok:
            eligible.append(event)
    if not eligible:
        return None
    weights = [
        RARITY_WEIGHTS.get(e.get("rarity", "common"), 60) +
        (edu_bonus if e.get("rarity") in ("rare", "legendary") else 0)
        for e in eligible
    ]
    return random.choices(eligible, weights=weights, k=1)[0]


def open_event_issue(event: dict) -> int:
    imm = event.get("immediate_effects", {})
    imm_str = "  ".join(
        f"{k} {'+' if isinstance(v, (int,float)) and v>0 else ''}{v}"
        for k, v in imm.items() if k != "all_random"
    ) or "none"
    def fmt_effects(d):
        return ", ".join(
            f"{k} {'+' if isinstance(v,(int,float)) and v>0 else ''}{v}"
            for k, v in d.items() if isinstance(v, (int, float)) and k != "all_random"
        ) or "none"
    body = (
        f"## ⚡ Random Event: {event['title']}\n\n"
        f"{event['description']}\n\n"
        f"*{event.get('flavor', '')}*\n\n"
        f"---\n\n"
        f"**Category:** {event.get('category','?').title()}  ·  "
        f"**Rarity:** {event.get('rarity','?').title()}\n\n"
        f"**Immediate effects (already applied):** {imm_str}\n\n"
        f"**Response window:** 4 hours from now\n\n"
        f"**If NO law is passed:** {fmt_effects(event.get('default_consequence', {}))}\n\n"
        f"**If ANY law is passed:** {fmt_effects(event.get('response_consequence', {}))}\n\n"
        f"> 💡 **Hint:** {event.get('response_hint', 'Pass any law to respond.')}\n"
    )
    tmp = Path("scripts/_event_body.txt")
    tmp.write_text(body, encoding="utf-8")
    result = run(["gh", "issue", "create", "--repo", REPO,
                  "--title", f"[EVENT] {event['title']}",
                  "--label", "event",
                  "--body-file", str(tmp)])
    tmp.unlink(missing_ok=True)
    try:
        return int(result.strip().split("/")[-1])
    except (ValueError, IndexError):
        return 0


def close_event_issue(issue_number: int, responded: bool, event: dict):
    if not issue_number:
        return
    def fmt_effects(d):
        return ", ".join(
            f"{k} {'+' if isinstance(v,(int,float)) and v>0 else ''}{v}"
            for k, v in d.items() if isinstance(v, (int, float)) and k != "all_random"
        ) or "none"
    if responded:
        key = "response_consequence"
        icon = "✅"
        outcome = "A law was passed in time. Response consequence applied"
    else:
        key = "default_consequence"
        icon = "⏰"
        outcome = "No law was passed. Default consequence applied"
    effects = fmt_effects(event.get(key, {}))
    run(["gh", "issue", "comment", str(issue_number), "--repo", REPO,
         "--body", f"{icon} **Event resolved.** {outcome}: {effects}"])
    run(["gh", "issue", "close", str(issue_number), "--repo", REPO])
    run(["gh", "issue", "edit", str(issue_number), "--repo", REPO,
         "--remove-label", "event"])


def check_event_expiry(laws_enacted_this_tick: int) -> bool:
    active = load_active_event()
    if not active or not active.get("fired_at"):
        return False
    fired_dt = datetime.fromisoformat(active["fired_at"])
    now = datetime.now(timezone.utc)
    duration = active.get("duration_hours", 4)
    if now < fired_dt + timedelta(hours=duration):
        return False
    responded = laws_enacted_this_tick > 0
    consequence_key = "response_consequence" if responded else "default_consequence"
    print(f"  Event expired: {active['title']} — {'responded' if responded else 'no response'}")
    apply_event_effects(active, consequence_key)
    issue_number = active.get("issue_number", 0)
    if issue_number:
        close_event_issue(issue_number, responded, active)
    save_active_event({})
    fire_chained_event(active, responded)
    return True


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def pollution_level(state: dict) -> int:
    """Use stored pollution value; fall back to derived estimate."""
    stored = state.get("pollution")
    if stored is not None:
        return max(0, min(100, stored))
    return max(0, min(100, state.get("industry", 0) - state.get("green_policy", 0)))


def env_bg_color(pollution: int) -> str:
    """Interpolate SVG background: clean #161b22 → polluted #1e0e05."""
    t = max(0, min(100, pollution)) / 100
    r = int(round(0x16 + (0x1e - 0x16) * t))
    g = int(round(0x1b + (0x0e - 0x1b) * t))
    b = int(round(0x22 + (0x05 - 0x22) * t))
    return f"#{r:02x}{g:02x}{b:02x}"


# ── Entity management ────────────────────────────────────────────────────────

def next_entity_id(category: str) -> tuple[str, int]:
    prefix_map = {"buildings": "bld", "districts": "dst",
                  "institutions": "ins", "sectors": "sec"}
    prefix = prefix_map.get(category, category[:3])
    index_path = Path(f"world/entities/{category}/_index.json")
    data = read_json(index_path)
    seq = data["next_seq"]
    return f"{prefix}-{seq:03d}", seq


def entity_exists_by_name(category: str, name: str) -> str | None:
    idx = read_json(Path(f"world/entities/{category}/_index.json"))
    for eid in idx.get("entities", []):
        path = Path(f"world/entities/{category}/{eid}.json")
        if path.exists():
            entity = read_json(path)
            if entity.get("name", "").strip().lower() == name.strip().lower():
                return eid
    return None


def auto_create_entity(category: str, name: str, law_number: int, trigger: str) -> str:
    entity_id, seq = next_entity_id(category)
    now_iso = datetime.now(timezone.utc).isoformat()
    entity = {
        "id": entity_id,
        "name": name,
        "built_law": law_number,
        "built_at": now_iso,
        "auto_trigger": trigger,
    }
    write_json(Path(f"world/entities/{category}/{entity_id}.json"), entity)
    idx = read_json(Path(f"world/entities/{category}/_index.json"))
    idx["next_seq"] = seq + 1
    idx["count"] += 1
    idx["entities"].append(entity_id)
    write_json(Path(f"world/entities/{category}/_index.json"), idx)
    return entity_id


def auto_remove_entity(category: str, entity_id: str, law_number: int, reason: str):
    path = Path(f"world/entities/{category}/{entity_id}.json")
    if not path.exists():
        return
    entity = read_json(path)
    entity["demolished_law"] = law_number
    entity["demolished_at"] = datetime.now(timezone.utc).isoformat()
    entity["auto_reason"] = reason
    Path("world/archive").mkdir(parents=True, exist_ok=True)
    write_json(Path(f"world/archive/{entity_id}.json"), entity)
    path.unlink()
    idx = read_json(Path(f"world/entities/{category}/_index.json"))
    idx["count"] = max(0, idx["count"] - 1)
    if entity_id in idx["entities"]:
        idx["entities"].remove(entity_id)
    write_json(Path(f"world/entities/{category}/_index.json"), idx)


# ── Autonomous world tick ────────────────────────────────────────────────────

def world_autonomous_tick() -> bool:
    state = read_state()

    ind = state.get("industry", 0)
    grn = state.get("green_policy", 0)
    wel = state.get("welfare", 0)
    dfn = state.get("defense", 0)
    pol = state.get("pollution", 0)
    pop = state.get("population", 1000)
    stb = state.get("stability", 80)
    treasury = state.get("treasury", 0)

    # Pollution: industry generates it, green policy scrubs it
    pol_delta = +1 if ind - grn >= 20 else (-1 if grn - ind >= 20 else 0)
    new_pol = max(0, min(100, pol + pol_delta))

    # Population: base growth from welfare, bonus if welfare > 60, penalty from heavy pollution
    pop_delta = 50 if wel >= 40 else 0
    if wel > 60:
        pop_delta += 100
    if new_pol >= 70:
        pop_delta -= 50
    new_pop = max(0, pop + pop_delta)

    # Stability: drifts ±1 toward equilibrium; bonus +1 if welfare > 80
    target_stb = max(0, min(100, 30 + wel // 5 + dfn // 10 - new_pol // 10))
    new_stb = stb + (1 if stb < target_stb else (-1 if stb > target_stb else 0))
    if wel > 80:
        new_stb = min(100, new_stb + 1)

    # Idle income: industrial output + population tax (capped at 100,000)
    industry_income = ind // 10       # 0–8 GC/tick
    pop_income      = new_pop // 500  # 1 GC per 500 citizens
    new_treasury = min(100_000, treasury + industry_income + pop_income)

    # Era re-evaluation
    state.update({
        "pollution":  new_pol,
        "population": new_pop,
        "stability":  new_stb,
        "treasury":   new_treasury,
    })
    new_era = determine_era(state)
    era_changed = new_era != state.get("era", "Founding Era")
    state["era"] = new_era

    changed = (new_pol != pol or new_pop != pop or new_stb != stb or
               new_treasury != treasury or era_changed)
    if not changed:
        return False

    write_state(state)
    print(f"  Tick: pol={new_pol} pop={new_pop} stb={new_stb} "
          f"treasury={new_treasury}(+{industry_income+pop_income}) era={new_era}")
    return True


# ── World engine ─────────────────────────────────────────────────────────────

def run_world_engine(law_number: int | None) -> list[str]:
    """Auto-create/remove entities based on current policy metrics."""
    state = read_state()
    changes = []

    for metric, appear, category, name, remove in WORLD_GENERATION_RULES:
        value = state.get(metric, 0)
        existing_id = entity_exists_by_name(category, name)

        if value >= appear and not existing_id:
            eid = auto_create_entity(category, name, law_number, f"{metric} >= {appear}")
            changes.append(f"{name} emerged ({eid})")
            print(f"  World engine: {name} created ({metric}={value})")
        elif value < remove and existing_id:
            auto_remove_entity(category, existing_id, law_number, f"{metric} < {remove}")
            changes.append(f"{name} dismantled")
            print(f"  World engine: {name} removed ({metric}={value})")

    return changes


# ── Effect system ─────────────────────────────────────────────────────────────

def apply_effect(effect_data: dict | None, law_number: int):
    if not effect_data:
        return
    etype = effect_data.get("type", "declaration")

    if etype == "declaration":
        pass

    elif etype == "policy":
        changes = effect_data.get("changes", {})
        state = read_state()
        for metric, delta in changes.items():
            if metric in POLICY_METRICS:
                current = state.get(metric, 0)
                state[metric] = max(0, min(100, current + int(delta)))
        if state.get("treasury") is not None:
            state["treasury"] = max(0, state["treasury"] - POLICY_COST)
        write_state(state)

    elif etype == "evolve":
        entity_id = effect_data.get("id", "")
        changes = effect_data.get("changes", {})
        safe_changes = {k: v for k, v in changes.items() if k not in _EVOLVE_BLOCKED}
        for cat in ("buildings", "districts", "institutions", "sectors"):
            path = Path(f"world/entities/{cat}/{entity_id}.json")
            if path.exists():
                entity = read_json(path)
                entity.update(safe_changes)
                entity["last_evolved_law"] = law_number
                write_json(path, entity)
                break

    elif etype == "state_patch":
        patch = effect_data.get("patch", {})
        _ALLOWED = {
            "treasury", "currency", "founded_date",
            "education", "industry", "welfare", "green_policy", "defense",
            "pollution", "stability", "population",
        }
        _NUMERIC_0_100 = {"education", "industry", "welfare", "green_policy",
                          "defense", "pollution", "stability"}
        state = read_state()
        for key, val in patch.items():
            if key not in _ALLOWED:
                print(f"  [BLOCKED] state_patch key '{key}' not in allowlist — skipped")
                continue
            if key in _NUMERIC_0_100:
                try:
                    state[key] = max(0, min(100, int(val)))
                except (TypeError, ValueError):
                    print(f"  [BLOCKED] state_patch key '{key}' invalid value '{val}' — skipped")
            elif key == "population":
                try:
                    state[key] = max(0, min(10_000_000, int(val)))
                except (TypeError, ValueError):
                    print(f"  [BLOCKED] state_patch key '{key}' invalid value '{val}' — skipped")
            elif key == "treasury":
                try:
                    state[key] = max(0, min(100_000, int(val)))
                except (TypeError, ValueError):
                    print(f"  [BLOCKED] state_patch key '{key}' invalid value '{val}' — skipped")
            else:
                state[key] = val  # currency, founded_date — validated at proposal time
        write_state(state)


# ── SVG helpers ──────────────────────────────────────────────────────────────

def svg_radar(cx: float, cy: float, r: float,
              vals: list[float], colors: list[str], labels: list[str],
              font_size: int = 7) -> str:
    """Return SVG markup for a mini pentagon radar chart (L)."""
    n = len(vals)
    angles = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]
    parts: list[str] = []

    # Background grid rings
    for frac in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(
            f"{cx + r * frac * math.cos(a):.1f},{cy + r * frac * math.sin(a):.1f}"
            for a in angles
        )
        parts.append(f'<polygon points="{pts}" fill="none" stroke="#30363d" stroke-width="0.5"/>')

    # Axis spokes
    for a in angles:
        ax, ay = cx + r * math.cos(a), cy + r * math.sin(a)
        parts.append(
            f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{ax:.1f}" y2="{ay:.1f}"'
            f' stroke="#30363d" stroke-width="0.5"/>'
        )

    # Data polygon (filled area)
    data_pts = " ".join(
        f"{cx + r * (v / 100) * math.cos(a):.1f},{cy + r * (v / 100) * math.sin(a):.1f}"
        for v, a in zip(vals, angles)
    )
    parts.append(
        f'<polygon points="{data_pts}" fill="rgba(56,139,253,0.14)" stroke="#388bfd" stroke-width="1.2"/>'
    )

    # Data point dots
    for v, a, c in zip(vals, angles, colors):
        dx, dy = cx + r * (v / 100) * math.cos(a), cy + r * (v / 100) * math.sin(a)
        parts.append(f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="2.2" fill="{c}"/>')

    # Axis labels at outer edge
    for lbl, a in zip(labels, angles):
        lx = cx + (r + 9) * math.cos(a)
        ly = cy + (r + 9) * math.sin(a)
        if math.cos(a) > 0.3:
            anchor = "start"
        elif math.cos(a) < -0.3:
            anchor = "end"
        else:
            anchor = "middle"
        dy_attr = ' dy="0.35em"' if abs(math.sin(a)) < 0.3 else (
            ' dy="0.7em"' if math.sin(a) > 0 else ' dy="-0.2em"'
        )
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}"{dy_attr} fill="#484f58"'
            f' font-family="monospace" font-size="{font_size}"'
            f' text-anchor="{anchor}">{lbl}</text>'
        )

    return "\n  ".join(parts)


# ── SVG: dashboard ───────────────────────────────────────────────────────────

def generate_dashboard_svg(stats: dict, date: str):
    state = read_state()
    era = state.get("era", "Founding Era")
    laws = state.get("laws_count", 0)
    treasury = state.get("treasury")

    founded_date = state.get("founded_date")
    if founded_date:
        delta = (datetime.now(timezone.utc).date() -
                 datetime.fromisoformat(founded_date).date())
        day_str = f"Day {delta.days + 1} of {era}"
    else:
        day_str = era

    passed = stats.get("proposals_passed", 0)
    rejected = stats.get("proposals_rejected", 0)
    total = passed + rejected
    pass_rate = round(passed / max(total, 1) * 100)

    total_entities = 0
    for cat, _ in CATEGORIES:
        try:
            total_entities += read_json(Path(f"world/entities/{cat}/_index.json")).get("count", 0)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    max_bar = 580
    scale = max_bar / max(passed, rejected, 1)
    passed_w  = max(int(passed  * scale), 4 if passed  > 0 else 0)
    rejected_w = max(int(rejected * scale), 4 if rejected > 0 else 0)

    treasury_str   = f"{treasury:,}" if isinstance(treasury, (int, float)) else "—"
    treasury_color = "#e3b341" if treasury is not None else "#484f58"

    edu = state.get("education", 0)
    ind = state.get("industry", 0)
    wel = state.get("welfare", 0)
    grn = state.get("green_policy", 0)
    dfn = state.get("defense", 0)
    pol = pollution_level(state)
    pop = state.get("population", 0)
    stb = state.get("stability", 0)

    def bar_w(val, max_w=270):
        return max(int(val / 100 * max_w), 2 if val > 0 else 0)

    def bar_w_sm(val):  # compact bar for right column
        return bar_w(val, max_w=130)

    def mc(val):  # metric color
        if val >= 60: return "#3fb950"
        if val >= 30: return "#e3b341"
        return "#484f58"

    pol_color = "#f85149" if pol >= 60 else "#e3b341" if pol >= 30 else "#3fb950"
    pop_str   = f"{pop:,}" if pop else "—"
    stb_color = "#3fb950" if stb >= 60 else "#e3b341" if stb >= 40 else "#f85149"

    radar = svg_radar(
        660, 183, 50,
        [edu, ind, wel, grn, dfn],
        ["#388bfd", "#bc8cff", "#3fb950", "#2dd4bf", "#f0883e"],
        ["EDU", "IND", "WEL", "GRN", "DEF"],
    )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="760" height="370">
  <rect width="760" height="370" rx="8" fill="#161b22"/>

  <text x="24" y="40" fill="#c9d1d9" font-family="monospace" font-size="20" font-weight="bold">GITIZENS</text>
  <text x="24" y="58" fill="#8b949e" font-family="monospace" font-size="12">{day_str}</text>

  <line x1="24" y1="70" x2="736" y2="70" stroke="#30363d" stroke-width="1"/>

  <text x="24"  y="90"  fill="#484f58" font-family="monospace" font-size="10">LAWS ENACTED</text>
  <text x="24"  y="116" fill="#c9d1d9" font-family="monospace" font-size="26" font-weight="bold">{laws}</text>

  <text x="210" y="90"  fill="#484f58" font-family="monospace" font-size="10">STRUCTURES</text>
  <text x="210" y="116" fill="#c9d1d9" font-family="monospace" font-size="26" font-weight="bold">{total_entities}</text>

  <text x="396" y="90"  fill="#484f58" font-family="monospace" font-size="10">TREASURY</text>
  <text x="396" y="116" fill="{treasury_color}" font-family="monospace" font-size="26" font-weight="bold">{treasury_str}</text>

  <text x="582" y="90"  fill="#484f58" font-family="monospace" font-size="10">PASS RATE</text>
  <text x="582" y="116" fill="#3fb950" font-family="monospace" font-size="26" font-weight="bold">{pass_rate}%</text>

  <line x1="24" y1="132" x2="736" y2="132" stroke="#30363d" stroke-width="1"/>

  <text x="24"  y="150" fill="#484f58" font-family="monospace" font-size="10">POLICY METRICS</text>
  <text x="700" y="150" fill="{pol_color}" font-family="monospace" font-size="10" text-anchor="end">POLLUTION {pol}/100</text>

  <text x="24"  y="168" fill="#8b949e" font-family="monospace" font-size="10">EDU</text>
  <rect x="56"  y="158" width="{bar_w(edu)}" height="12" rx="2" fill="{mc(edu)}"/>
  <text x="{56 + bar_w(edu) + 4}" y="168" fill="{mc(edu)}" font-family="monospace" font-size="10">{edu}</text>

  <text x="24"  y="186" fill="#8b949e" font-family="monospace" font-size="10">IND</text>
  <rect x="56"  y="176" width="{bar_w(ind)}" height="12" rx="2" fill="{mc(ind)}"/>
  <text x="{56 + bar_w(ind) + 4}" y="186" fill="{mc(ind)}" font-family="monospace" font-size="10">{ind}</text>

  <text x="24"  y="204" fill="#8b949e" font-family="monospace" font-size="10">WEL</text>
  <rect x="56"  y="194" width="{bar_w(wel)}" height="12" rx="2" fill="{mc(wel)}"/>
  <text x="{56 + bar_w(wel) + 4}" y="204" fill="{mc(wel)}" font-family="monospace" font-size="10">{wel}</text>

  <text x="400" y="168" fill="#8b949e" font-family="monospace" font-size="10">GRN</text>
  <rect x="432" y="158" width="{bar_w_sm(grn)}" height="12" rx="2" fill="{mc(grn)}"/>
  <text x="{432 + bar_w_sm(grn) + 4}" y="168" fill="{mc(grn)}" font-family="monospace" font-size="10">{grn}</text>

  <text x="400" y="186" fill="#8b949e" font-family="monospace" font-size="10">DEF</text>
  <rect x="432" y="176" width="{bar_w_sm(dfn)}" height="12" rx="2" fill="{mc(dfn)}"/>
  <text x="{432 + bar_w_sm(dfn) + 4}" y="186" fill="{mc(dfn)}" font-family="monospace" font-size="10">{dfn}</text>

  {radar}

  <line x1="24" y1="218" x2="736" y2="218" stroke="#30363d" stroke-width="1"/>

  <text x="24" y="238" fill="#8b949e" font-family="monospace" font-size="11">Passed  </text>
  <rect x="100" y="225" width="{passed_w}" height="20" rx="3" fill="#3fb950"/>
  <text x="{passed_w + 108}" y="240" fill="#3fb950" font-family="monospace" font-size="11">{passed}</text>

  <text x="24" y="274" fill="#8b949e" font-family="monospace" font-size="11">Rejected</text>
  <rect x="100" y="261" width="{rejected_w}" height="20" rx="3" fill="#f85149"/>
  <text x="{rejected_w + 108}" y="276" fill="#f85149" font-family="monospace" font-size="11">{rejected}</text>

  <line x1="24" y1="296" x2="736" y2="296" stroke="#30363d" stroke-width="1"/>

  <text x="24"  y="314" fill="#484f58" font-family="monospace" font-size="10">POPULATION</text>
  <text x="280" y="314" fill="#484f58" font-family="monospace" font-size="10">POLLUTION</text>
  <text x="536" y="314" fill="#484f58" font-family="monospace" font-size="10">STABILITY</text>

  <text x="24"  y="334" fill="#c9d1d9" font-family="monospace" font-size="18" font-weight="bold">{pop_str}</text>
  <text x="280" y="334" fill="{pol_color}" font-family="monospace" font-size="18" font-weight="bold">{pol}/100</text>
  <text x="536" y="334" fill="{stb_color}" font-family="monospace" font-size="18" font-weight="bold">{stb}/100</text>

  <text x="24" y="358" fill="#484f58" font-family="monospace" font-size="10">Total proposals: {total} | Updated: {date}</text>
</svg>"""
    Path("world/stats.svg").write_text(svg, encoding="utf-8")


# ── SVG: world map ────────────────────────────────────────────────────────────

def generate_map_svg(date: str):
    W, H = 760, 370
    PAD, INNER_GAP = 24, 14

    CELL_W = (W - 2 * PAD - INNER_GAP) // 2
    CELL_H = 140
    CELLS_TOP = 58

    CELL_PAD = 12
    CHIP_W, CHIP_H, CHIP_GAP = 58, 30, 6

    chips_per_row = (CELL_W - 2 * CELL_PAD + CHIP_GAP) // (CHIP_W + CHIP_GAP)
    chip_rows     = (CELL_H - 38) // (CHIP_H + CHIP_GAP)
    MAX_CHIPS     = chips_per_row * chip_rows

    def _trunc(s: str, n: int = 9) -> str:
        return s[:n] + "…" if len(s) > n else s

    state = read_state()
    pol = pollution_level(state)
    bg_color = env_bg_color(pol)

    categories_data = []
    total_entities = 0
    for cat, label in CATEGORIES:
        try:
            idx = read_json(Path(f"world/entities/{cat}/_index.json"))
            entity_records = []
            for eid in idx.get("entities", []):
                p = Path(f"world/entities/{cat}/{eid}.json")
                if not p.exists():
                    continue
                try:
                    e = read_json(p)
                    entity_records.append({"id": eid, "name": e.get("name", eid)})
                except (json.JSONDecodeError, OSError):
                    entity_records.append({"id": eid, "name": eid})
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            entity_records = []
        categories_data.append((cat, label, entity_records))
        total_entities += len(entity_records)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}">',
        f'  <rect width="{W}" height="{H}" rx="8" fill="{bg_color}"/>',
        f'  <text x="{PAD}" y="36" fill="#c9d1d9" font-family="monospace" font-size="14" font-weight="bold">GITIZENS — World Map</text>',
        f'  <text x="{W - PAD}" y="36" fill="#484f58" font-family="monospace" font-size="11" text-anchor="end">{total_entities} structure{"s" if total_entities != 1 else ""} | Updated: {date}</text>',
        f'  <line x1="{PAD}" y1="48" x2="{W - PAD}" y2="48" stroke="#30363d" stroke-width="1"/>',
    ]

    for i, (cat, label, entities) in enumerate(categories_data):
        col = i % 2
        row = i // 2
        cx = PAD + col * (CELL_W + INNER_GAP)
        cy = CELLS_TOP + row * (CELL_H + INNER_GAP)
        color = CATEGORY_COLORS.get(cat, "#8b949e")
        count = len(entities)

        lines += [
            f'  <rect x="{cx}" y="{cy}" width="{CELL_W}" height="{CELL_H}" rx="4" fill="#0d1117" stroke="#30363d" stroke-width="1"/>',
            f'  <rect x="{cx}" y="{cy}" width="{CELL_W}" height="3" rx="2" fill="{color}"/>',
            f'  <text x="{cx + CELL_PAD}" y="{cy + 22}" fill="{color}" font-family="monospace" font-size="11" font-weight="bold">{label.upper()}</text>',
            f'  <text x="{cx + CELL_W - CELL_PAD}" y="{cy + 22}" fill="{color}" font-family="monospace" font-size="14" font-weight="bold" text-anchor="end">{count}</text>',
        ]

        if count == 0:
            lines.append(f'  <text x="{cx + CELL_PAD}" y="{cy + 58}" fill="#30363d" font-family="monospace" font-size="10">— none yet —</text>')
        else:
            show     = entity_records[:MAX_CHIPS - 1] if count > MAX_CHIPS else entity_records
            overflow = count - len(show)
            for j, rec in enumerate(show):
                fx = cx + CELL_PAD + (j % chips_per_row) * (CHIP_W + CHIP_GAP)
                fy = cy + 32       + (j // chips_per_row) * (CHIP_H + CHIP_GAP)
                lines += [
                    f'  <rect x="{fx}" y="{fy}" width="{CHIP_W}" height="{CHIP_H}" rx="3" fill="#161b22" stroke="{color}" stroke-width="1"/>',
                    f'  <text x="{fx + CHIP_W // 2}" y="{fy + 12}" fill="#c9d1d9" font-family="monospace" font-size="8" text-anchor="middle">{_trunc(rec["name"])}</text>',
                    f'  <text x="{fx + CHIP_W // 2}" y="{fy + 24}" fill="#484f58" font-family="monospace" font-size="7" text-anchor="middle">{rec["id"]}</text>',
                ]
            if overflow > 0:
                j  = len(show)
                fx = cx + CELL_PAD + (j % chips_per_row) * (CHIP_W + CHIP_GAP)
                fy = cy + 32       + (j // chips_per_row) * (CHIP_H + CHIP_GAP)
                lines += [
                    f'  <rect x="{fx}" y="{fy}" width="{CHIP_W}" height="{CHIP_H}" rx="3" fill="#161b22" stroke="#484f58" stroke-width="1"/>',
                    f'  <text x="{fx + CHIP_W // 2}" y="{fy + 18}" fill="#484f58" font-family="monospace" font-size="9" text-anchor="middle">+{overflow} more</text>',
                ]

    lines.append('</svg>')
    Path("world/map.svg").write_text("\n".join(lines), encoding="utf-8")


# ── Era progression ──────────────────────────────────────────────────────────

def determine_era(state: dict) -> str:
    pol = state.get("pollution", 0)
    stb = state.get("stability", 0)
    edu = state.get("education", 0)
    ind = state.get("industry", 0)
    wel = state.get("welfare", 0)
    grn = state.get("green_policy", 0)
    dfs = state.get("defense", 0)

    if pol >= 75 or stb <= 25:
        return "Crisis Age"
    if all(m >= 80 for m in [edu, ind, wel, grn, dfs]) and stb >= 80:
        return "Golden Age"
    if all(m >= 65 for m in [edu, ind, wel, grn, dfs]):
        return "Modern Era"
    if ind >= 60 and edu >= 50:
        return "Industrial Era"
    return "Founding Era"


# ── Tags ──────────────────────────────────────────────────────────────────────

def check_threshold_tags(state_before: dict, state_after: dict) -> list[tuple[str, str]]:
    applied = list(state_after.get("tags_applied", []))
    new_tags = []
    for field, direction, threshold, tag_name in THRESHOLD_TAGS:
        if tag_name in applied:
            continue
        bv = state_before.get(field)
        av = state_after.get(field)
        if bv is None or av is None:
            continue
        triggered = (direction == "below" and bv >= threshold > av) or \
                    (direction == "above" and bv <= threshold < av)
        if triggered:
            new_tags.append((tag_name, f"World milestone: {field} crossed {threshold}"))
            applied.append(tag_name)
    state_after["tags_applied"] = applied
    return new_tags


def apply_tags(effect_data: dict | None, state_before: dict, state_after: dict,
               law_number: int, clean_title: str, threshold_tags: list[tuple[str, str]]):
    tags: list[tuple[str, str]] = []

    era_before = state_before.get("era", "")
    era_after  = state_after.get("era", "")
    if era_before != era_after and era_after:
        tags.append((f"era/{slugify(era_after)}",
                     f"Era transition: '{era_before}' -> '{era_after}' | law-{law_number:03d}: {clean_title}"))

    tags.extend(threshold_tags)

    if effect_data and effect_data.get("type") == "declaration":
        decl_tag = str(effect_data.get("tag", "")).strip()
        if decl_tag and "/" in decl_tag:
            tags.append((decl_tag, f"Constitutional declaration | law-{law_number:03d}: {clean_title}"))

    for tag_name, tag_msg in tags:
        print(f"  Tag: {tag_name}")
        run(["git", "tag", "-a", tag_name, "-m", tag_msg])


# ── LLM helpers ───────────────────────────────────────────────────────────────

_LLM_EXCLUDE = {"known_stargazers", "tags_applied"}

def _state_for_llm(state: dict) -> dict:
    """Return state dict with large/irrelevant fields stripped for LLM prompts."""
    return {k: v for k, v in state.items() if k not in _LLM_EXCLUDE}


def generate_narrative(title: str, for_votes: int, against_votes: int, state: dict) -> str:
    metrics_str = " | ".join(f"{k}={state.get(k, 0)}" for k in sorted(POLICY_METRICS))
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": (
            "You are the narrator of a GitHub-based civilization called Gitizens.\n"
            "A new law has just been enacted by citizen vote.\n\n"
            f"World state: era={state.get('era')}, laws={state.get('laws_count')}, {metrics_str}\n"
            f"Title: {title}\n"
            f"Vote: {for_votes} for, {against_votes} against\n\n"
            "Write a 2-sentence news bulletin announcing this law's passage. "
            "Tone: serious newspaper. No emoji, no markdown headers."
        )}],
        max_tokens=150,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


def update_world_summary(state: dict) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": (
            "You are summarizing the state of a GitHub-based civilization called Gitizens.\n"
            f"Current state: {json.dumps(_state_for_llm(state), ensure_ascii=False)}\n\n"
            "Write a single sentence (max 25 words) describing the current state of the nation. "
            "Mention notable policy levels or emerging structures if relevant. No emoji."
        )}],
        max_tokens=70,
        temperature=0.7,
    )
    return response.choices[0].message.content.strip()


# ── World / README / History ──────────────────────────────────────────────────

def generate_world_md(state: dict, law_number: int | None, date: str):
    lines: list[str] = ["# World State", ""]
    if law_number:
        lines.append(f"*Last updated: {date} — [Law {law_number:03d}](laws/law-{law_number:03d}.md)*")
    else:
        lines.append(f"*Last updated: {date}*")

    lines += ["", "---", "", "## Metrics", ""]
    lines += ["| Field | Value |", "|-------|-------|"]
    lines.append(f"| Era | {state.get('era', '')} |")
    lines.append(f"| Laws enacted | {state.get('laws_count', 0)} |")
    lines.append(f"| Last enacted | {state.get('last_enacted') or '—'} |")
    lines.append(f"| Treasury | {state.get('treasury', 0):,} {state.get('currency', 'Git Coins')} |")

    lines += ["", "### Policy", ""]
    lines += ["| Metric | Value |", "|--------|-------|"]
    for m in ("education", "industry", "welfare", "green_policy", "defense"):
        lines.append(f"| {m.replace('_', ' ').title()} | {state.get(m, 0)}/100 |")
    lines.append(f"| Pollution *(derived)* | {pollution_level(state)}/100 |")

    for k, v in state.items():
        if k not in BASE_STATE_FIELDS:
            lines.append(f"| {k.replace('_', ' ').title()} | {v} |")

    lines += ["", "---", "", "## Entities", ""]
    for cat, label in CATEGORIES:
        try:
            idx = read_json(Path(f"world/entities/{cat}/_index.json"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            idx = {}
        entity_ids = idx.get("entities", [])
        lines.append(f"### {label}")
        lines.append("")
        if entity_ids:
            lines += ["| ID | Name | Built by | Trigger |", "|----|------|----------|---------|"]
            for eid in entity_ids:
                path = Path(f"world/entities/{cat}/{eid}.json")
                if path.exists():
                    e = read_json(path)
                    law = e.get("built_law")
                    law_ref = f"[Law {law:03d}](laws/law-{law:03d}.md)" if law else "—"
                    trigger = e.get("auto_trigger", "—")
                    lines.append(f"| `{eid}` | {e.get('name', eid)} | {law_ref} | {trigger} |")
        else:
            lines.append("*(none yet)*")
        lines.append("")

    lines += ["---", "", "## Archive", ""]
    try:
        archived = sorted(f for f in Path("world/archive").glob("*.json") if f.name != ".gitkeep")
    except OSError:
        archived = []
    if archived:
        lines += ["| ID | Name | Demolished by | Reason |", "|----|------|---------------|--------|"]
        for f in archived:
            e = read_json(f)
            law = e.get("demolished_law")
            law_ref = f"[Law {law:03d}](laws/law-{law:03d}.md)" if law else "—"
            lines.append(f"| `{f.stem}` | {e.get('name', f.stem)} | {law_ref} | {e.get('auto_reason', '—')} |")
    else:
        lines.append("*(none)*")

    Path("world/WORLD.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_readme(state: dict, stats: dict, law_number: int | None = None, date: str = ""):
    readme_path = Path("README.md")
    content = readme_path.read_text(encoding="utf-8")
    era = state.get("era", "")
    laws_count = state.get("laws_count", 0)
    next_tick = state.get("next_tick_at", "—")
    block = (
        f"**Era:** {era} | **Laws enacted:** {laws_count} | [World state](world/WORLD.md)  \n"
        f"**Next tick:** {next_tick} UTC"
    )
    new_content = re.sub(
        r"<!-- STATE_START -->.*?<!-- STATE_END -->",
        f"<!-- STATE_START -->\n{block}\n<!-- STATE_END -->",
        content, flags=re.DOTALL,
    )
    readme_path.write_text(new_content, encoding="utf-8")


def append_history(law_number: int | None, title: str, issue_number: int,
                   for_votes: int, against_votes: int, passed: bool, date: str):
    path = Path("history/INDEX.md")
    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        print(f"  [WARN] history/INDEX.md not found — skipping history append")
        return
    issue_link = f"[#{issue_number}](https://github.com/{REPO}/issues/{issue_number})"
    if passed:
        law_link = f"[law-{law_number:03d}](../world/laws/law-{law_number:03d}.md)"
        row = f"| {law_number} | {law_link} | {issue_link} {title} | {for_votes}+1 {against_votes}-1 | {date} |"
    else:
        row = f"| - | *(rejected)* | {issue_link} {title} | {for_votes}+1 {against_votes}-1 | {date} |"
    path.write_text(content + row + "\n", encoding="utf-8")


# ── Laws index ────────────────────────────────────────────────────────────────

def update_laws_index(law_number: int, title: str, issue_number: int,
                      issue_url: str, era: str, date: str):
    path = Path("world/laws_index.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (json.JSONDecodeError, OSError):
        data = []
    data.append({
        "number": law_number,
        "title": title,
        "issue_number": issue_number,
        "issue_url": issue_url,
        "enacted_date": date,
        "era": era,
    })
    if len(data) > 20:
        data = data[-20:]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ── World dispatch ─────────────────────────────────────────────────────────────

def get_or_create_dispatch_issue() -> int:
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "dispatch",
        "--state", "open", "--json", "number", "--limit", "1",
    ])
    if issues:
        return issues[0]["number"]
    body = (
        "This issue is the permanent news feed for **Gitizens**.\n\n"
        "Every 4 hours, the world narrator posts a dispatch summarizing "
        "what happened in the latest tick — laws passed, events fired, "
        "population changes, and more.\n\n"
        "*React with 👍 to follow the chronicle.*"
    )
    tmp = Path("scripts/_dispatch_body.txt")
    tmp.write_text(body, encoding="utf-8")
    result = run(["gh", "issue", "create", "--repo", REPO,
                  "--title", "[World Chronicle] The Gitizens Dispatch",
                  "--label", "dispatch",
                  "--body-file", str(tmp)])
    tmp.unlink(missing_ok=True)
    try:
        return int(result.strip().split("/")[-1])
    except (ValueError, IndexError):
        return 0


def post_world_dispatch(state: dict, tick_changed: bool, laws_passed: int,
                        event_title: str, feedback_count: int):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = []
    try:
        history = json.loads(Path("world/history.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    tick_num = (history[-1]["tick"] + 1) if history else 1  # this tick hasn't been snapshotted yet

    metrics_str = (
        f"population {state.get('population',0):,} · "
        f"treasury {state.get('treasury',0)} GC · "
        f"stability {state.get('stability',0)}/100 · "
        f"pollution {state.get('pollution',0)}/100"
    )
    changes_parts = []
    if tick_changed:
        changes_parts.append("autonomous tick applied")
    if laws_passed:
        changes_parts.append(f"{laws_passed} law{'s' if laws_passed > 1 else ''} enacted")
    if feedback_count:
        changes_parts.append(f"{feedback_count} citizen feedback{'s' if feedback_count > 1 else ''} applied")
    if event_title:
        changes_parts.append(f"event: {event_title}")
    changes_summary = " · ".join(changes_parts) if changes_parts else "quiet tick"

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": (
            "You are the narrator of Gitizens, a GitHub-based civilization.\n"
            f"Tick #{tick_num} just completed on {today}.\n"
            f"World state: era={state.get('era')}, {metrics_str}\n"
            f"This tick: {changes_summary}\n\n"
            "Write a 2-3 sentence news dispatch in the style of a newspaper. "
            "Mention specific numbers. Tone: serious but vivid. No emoji, no markdown headers."
        )}],
        max_tokens=120,
        temperature=0.7,
    )
    narrative = response.choices[0].message.content.strip()

    comment = (
        f"## Dispatch — {today} · Tick {tick_num}\n\n"
        f"{narrative}\n\n"
        f"**Metrics:** {metrics_str}  \n"
        f"**This tick:** {changes_summary}"
    )

    issue_num = get_or_create_dispatch_issue()
    if issue_num:
        run(["gh", "issue", "comment", str(issue_num), "--repo", REPO, "--body", comment])
        print(f"  Dispatch posted to issue #{issue_num}")

    dispatches_path = Path("world/dispatches.json")
    try:
        dispatches = json.loads(dispatches_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        dispatches = []
    dispatches.append({
        "tick": tick_num,
        "date": today,
        "narrative": narrative,
        "changes": changes_summary,
    })
    if len(dispatches) > 10:
        dispatches = dispatches[-10:]
    dispatches_path.write_text(json.dumps(dispatches, indent=2) + "\n", encoding="utf-8")


# ── Star income ───────────────────────────────────────────────────────────────

def collect_star_income():
    state = read_state()
    if state.get("treasury") is None:
        return

    raw = run(["gh", "api", f"repos/{REPO}/stargazers", "--paginate",
               "--jq", ".[].login"])
    current_logins = {line.strip() for line in raw.splitlines() if line.strip()}

    # First run: initialize tracking without income (migration-safe)
    if state.get("known_stargazers") is None:
        state["known_stargazers"] = sorted(current_logins)
        state["stars_last_counted"] = len(current_logins)
        write_state(state)
        print(f"  Star tracking initialized: {len(current_logins)} existing stars, no income (first run)")
        return

    ever_starred = set(state["known_stargazers"])
    new_logins = current_logins - ever_starred

    # Union never shrinks — re-starring after unstar earns no income
    state["known_stargazers"] = sorted(ever_starred | current_logins)
    state["stars_last_counted"] = len(current_logins)

    if not new_logins:
        write_state(state)
        return

    income = len(new_logins) * 10
    currency = state.get("currency", "Git Coins")
    state["treasury"] = min(100_000, state.get("treasury", 0) + income)
    write_state(state)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    generate_dashboard_svg(read_stats(), today)
    run(["git", "add", "world/state.json", "world/stats.svg"])
    run(["git", "commit", "-m",
         f"[WORLD] treasury: +{income} {currency} from {len(new_logins)} new star(s)"])
    print(f"  Star income: +{len(new_logins)} new stars → +{income} {currency}")


# ── AI citizen processing ─────────────────────────────────────────────────────

def get_ai_proposals() -> list:
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "ai-proposal",
        "--state", "open", "--json", "number,title,body,createdAt,reactions", "--limit", "50",
    ])
    return sorted(issues, key=lambda x: x["number"])


def process_ai_proposal(issue: dict):
    number = issue["number"]
    title = issue["title"]
    body = issue.get("body") or ""
    created_at = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    if not SKIP_TIMING and (now - created_at) < timedelta(hours=AI_VOTING_HOURS):
        print(f"  AI-proposal #{number}: window not over, skipping")
        return

    _, against_votes, _, against_voters = get_reactions(number)
    today = now.strftime("%Y-%m-%d")
    clean_title = re.sub(r"^\[AI-PROPOSAL\]\s*", "", title).strip()
    issue_url = f"https://github.com/{REPO}/issues/{number}"

    if against_votes > 0:
        print(f"  AI-proposal #{number}: VETOED ({against_votes} 👎)")
        stats = read_stats()
        stats["proposals_total"] = stats.get("proposals_total", 0) + 1
        stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
        write_stats(stats)
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", f"**AI proposal vetoed** by citizen vote ({against_votes} 👎). No effect applied."])
        run(["gh", "issue", "edit", str(number), "--repo", REPO,
             "--add-label", "rejected", "--remove-label", "ai-proposal"])
        run(["gh", "issue", "close", str(number), "--repo", REPO])
        return

    law_number = next_law_number()
    state_before = read_state()
    effect_data = parse_effect(body)

    if effect_data and effect_data.get("type") == "policy":
        ok, reason = check_proposal_cooldown(effect_data)
        if not ok:
            print(f"  AI-proposal #{number}: COOLDOWN BLOCKED — {reason}")
            run(["gh", "issue", "comment", str(number), "--repo", REPO,
                 "--body", f"**AI proposal blocked: metric on cooldown.**\n\n{reason}"])
            run(["gh", "issue", "edit", str(number), "--repo", REPO, "--remove-label", "ai-proposal"])
            run(["gh", "issue", "close", str(number), "--repo", REPO])
            return

    print(f"  AI-proposal #{number}: PASSED (no veto) -> law-{law_number:03d}")
    narrative = generate_narrative(clean_title, 0, 0, state_before)
    active_event_now = load_active_event()
    effect_data = apply_crisis_multiplier(effect_data, active_event_now)
    apply_effect(effect_data, law_number)
    world_changes = run_world_engine(law_number)

    state = read_state()
    state["era"] = determine_era(state)
    state["laws_count"] = law_number
    state["last_enacted"] = today
    state["world_summary"] = update_world_summary(state)
    threshold_tags = check_threshold_tags(state_before, state)
    write_state(state)

    stats = read_stats()
    stats["proposals_total"] = stats.get("proposals_total", 0) + 1
    stats["proposals_passed"] = stats.get("proposals_passed", 0) + 1
    write_stats(stats)

    generate_dashboard_svg(stats, today)
    generate_map_svg(today)
    generate_world_md(state, law_number, today)
    update_readme(state, stats, law_number, today)
    update_laws_index(law_number, clean_title, number, issue_url, state["era"], today)
    update_proposal_cooldown(effect_data, today)

    Path(f"world/laws/law-{law_number:03d}.md").write_text(
        f"# Law {law_number:03d}: {clean_title}\n\n"
        f"**Enacted:** {today}  \n"
        f"**Proposal:** [#{number}]({issue_url})  \n"
        f"**Proposed by:** AI citizen  \n"
        f"**Vote:** auto-passed (no veto)  \n"
        "\n---\n\n"
        f"{body}\n\n"
        "---\n\n"
        f"*{narrative}*\n",
        encoding="utf-8",
    )
    append_history(law_number, clean_title, number, 0, 0, True, today)
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", f"[LAW] law-{law_number:03d}: {clean_title} (AI, #{number})"])
    apply_tags(effect_data, state_before, state, law_number, clean_title, threshold_tags)

    world_note = ("\n\n**World changes:** " + ", ".join(world_changes)) if world_changes else ""
    run(["gh", "issue", "comment", str(number), "--repo", REPO,
         "--body",
         f"**Law {law_number:03d} enacted** (AI proposal — no veto received).\n\n"
         f"{narrative}{world_note}"])
    run(["gh", "issue", "edit", str(number), "--repo", REPO,
         "--add-label", "passed", "--remove-label", "ai-proposal"])
    run(["gh", "issue", "close", str(number), "--repo", REPO])


def get_feedbacks() -> list:
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "feedback",
        "--state", "open", "--json", "number,title,body,createdAt,reactions", "--limit", "50",
    ])
    return sorted(issues, key=lambda x: x["number"])


def process_feedback(issue: dict) -> bool:
    """Returns True if feedback was applied."""
    number = issue["number"]
    title = issue["title"]
    body = issue.get("body") or ""
    created_at = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    if not SKIP_TIMING and (now - created_at) < timedelta(hours=AI_VOTING_HOURS):
        print(f"  Feedback #{number}: window not over, skipping")
        return False

    _, against_votes, for_voters, against_voters = get_reactions(number)
    clean_title = re.sub(r"^\[FEEDBACK\]\s*", "", title).strip()
    track_citizen_activity(for_voters, against_voters)

    if against_votes > 0:
        print(f"  Feedback #{number}: DISMISSED ({against_votes} 👎)")
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", f"**Feedback dismissed** by citizens ({against_votes} 👎). No effect applied."])
        run(["gh", "issue", "edit", str(number), "--repo", REPO,
             "--add-label", "rejected", "--remove-label", "feedback"])
        run(["gh", "issue", "close", str(number), "--repo", REPO])
        return False

    effect_data = parse_effect(body)
    active_event_now = load_active_event()
    effect_data = apply_crisis_multiplier(effect_data, active_event_now)
    if effect_data and effect_data.get("type") == "policy":
        changes = effect_data.get("changes", {})
        state = read_state()
        for metric, delta in changes.items():
            if metric in POLICY_METRICS:
                state[metric] = max(0, min(100, state.get(metric, 0) + int(delta)))
            elif metric in ("stability", "pollution"):
                state[metric] = max(0, min(100, state.get(metric, 0) + int(delta)))
        write_state(state)
        run_world_engine(None)

        changes_str = ", ".join(
            f"{k} {'+' if int(v) > 0 else ''}{v}" for k, v in changes.items()
        )
        print(f"  Feedback #{number}: APPLIED ({changes_str})")
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", f"**Citizen feedback acknowledged.** Effects applied: {changes_str}"])
    else:
        print(f"  Feedback #{number}: APPLIED (no mechanical effect)")
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", "**Citizen feedback noted.** The world has taken notice."])

    run(["gh", "issue", "edit", str(number), "--repo", REPO,
         "--add-label", "applied", "--remove-label", "feedback"])
    run(["gh", "issue", "close", str(number), "--repo", REPO])
    return True


# ── Issue processing ──────────────────────────────────────────────────────────

def get_open_proposals() -> list:
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "proposal",
        "--state", "open", "--json", "number,title,body,createdAt,author", "--limit", "100",
    ])
    return sorted(issues, key=lambda x: x["number"])


def get_reactions(issue_number: int) -> tuple[int, int, list[str], list[str]]:
    # Use --jq to extract one object per line so --paginate concatenation is safe
    raw = run(["gh", "api", f"repos/{REPO}/issues/{issue_number}/reactions",
               "--paginate", "--jq", ".[] | {login: .user.login, content: .content}"])
    user_votes: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            user_votes[r["login"]] = r["content"]
        except (json.JSONDecodeError, KeyError):
            continue
    for_voters     = sorted(u for u, v in user_votes.items() if v == "+1")
    against_voters = sorted(u for u, v in user_votes.items() if v == "-1")
    return len(for_voters), len(against_voters), for_voters, against_voters


def parse_effect(body: str) -> dict | None:
    m = re.search(r"## Effect\s+```ya?ml\s+(.*?)```", body, re.DOTALL)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1).strip())
        return data if isinstance(data, dict) else None
    except yaml.YAMLError:
        return None


def next_law_number() -> int:
    return read_state().get("laws_count", 0) + 1


def process_issue(issue: dict):
    number = issue["number"]
    title  = issue["title"]
    body   = issue.get("body") or ""
    created_at = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    if not SKIP_TIMING and (now - created_at) < timedelta(days=VOTING_PERIOD_DAYS):
        print(f"  #{number}: voting period not over, skipping")
        return

    for_votes, against_votes, for_voters, against_voters = get_reactions(number)
    today = now.strftime("%Y-%m-%d")
    clean_title = re.sub(r"^\[PROPOSAL\]\s*", "", title).strip()

    if for_votes == 0 and against_votes == 0:
        print(f"  #{number}: zero votes — silent close")
        stats = read_stats()
        stats["proposals_silent"] = stats.get("proposals_silent", 0) + 1
        write_stats(stats)
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", "No votes were cast. Proposal closed without record."])
        run(["gh", "issue", "close", str(number), "--repo", REPO])
        run(["gh", "issue", "edit", str(number), "--repo", REPO, "--remove-label", "proposal"])
        return

    effect_data = parse_effect(body)

    if for_votes > against_votes:
        law_number  = next_law_number()
        state_before = read_state()

        # Treasury enforcement for policy proposals
        if effect_data and effect_data.get("type") == "policy":
            treasury = state_before.get("treasury", 0)
            currency = state_before.get("currency", "Git Coins")
            if treasury < POLICY_COST:
                print(f"  #{number}: TREASURY BLOCKED — needs {POLICY_COST}, has {treasury}")
                stats = read_stats()
                stats["proposals_total"]    = stats.get("proposals_total", 0) + 1
                stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
                write_stats(stats)
                run(["gh", "issue", "comment", str(number), "--repo", REPO,
                     "--body",
                     f"**Proposal blocked: insufficient treasury.**\n\n"
                     f"Enacting this policy costs **{POLICY_COST} {currency}**.\n"
                     f"Current treasury: **{treasury} {currency}**.\n\n"
                     f"Pass a treasury replenishment proposal first:\n"
                     f"```yaml\ntype: state_patch\npatch:\n  treasury: {treasury + POLICY_COST + 200}\n```"])
                run(["gh", "issue", "edit", str(number), "--repo", REPO,
                     "--add-label", "rejected", "--remove-label", "proposal"])
                run(["gh", "issue", "close", str(number), "--repo", REPO])
                return

        if effect_data and effect_data.get("type") == "policy":
            ok, reason = check_proposal_cooldown(effect_data)
            if not ok:
                print(f"  #{number}: COOLDOWN BLOCKED at tally — {reason}")
                stats = read_stats()
                stats["proposals_total"]    = stats.get("proposals_total", 0) + 1
                stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
                write_stats(stats)
                run(["gh", "issue", "comment", str(number), "--repo", REPO,
                     "--body", f"**Proposal blocked: metric on cooldown.**\n\n{reason}"])
                run(["gh", "issue", "edit", str(number), "--repo", REPO,
                     "--add-label", "rejected", "--remove-label", "proposal"])
                run(["gh", "issue", "close", str(number), "--repo", REPO])
                return

        print(f"  #{number}: PASSED ({for_votes}+1 {against_votes}-1) -> law-{law_number:03d}")
        narrative = generate_narrative(clean_title, for_votes, against_votes, state_before)

        active_event_now = load_active_event()
        effect_data = apply_crisis_multiplier(effect_data, active_event_now)
        apply_effect(effect_data, law_number)
        world_changes = run_world_engine(law_number)

        state = read_state()
        state["era"] = determine_era(state)
        state["laws_count"]    = law_number
        state["last_enacted"]  = today
        state["world_summary"] = update_world_summary(state)
        threshold_tags = check_threshold_tags(state_before, state)
        write_state(state)

        stats = read_stats()
        stats["proposals_total"]   = stats.get("proposals_total", 0) + 1
        stats["proposals_passed"]  = stats.get("proposals_passed", 0) + 1
        write_stats(stats)

        generate_dashboard_svg(stats, today)
        generate_map_svg(today)
        generate_world_md(state, law_number, today)
        update_readme(state, stats, law_number, today)

        issue_url  = f"https://github.com/{REPO}/issues/{number}"
        cost_line  = ""
        if effect_data and effect_data.get("type") == "policy":
            currency  = state_before.get("currency", "Git Coins")
            cost_line = f"**Treasury:** -{POLICY_COST} {currency} (balance: {state.get('treasury', 0)} {currency})  \n"

        proposer = issue.get("author", {}).get("login") or ""
        proposer_display = f"@{proposer}" if proposer else "*(unknown)*"
        signatories_block = format_signatories(for_voters, against_voters)
        Path(f"world/laws/law-{law_number:03d}.md").write_text(
            f"# Law {law_number:03d}: {clean_title}\n\n"
            f"**Enacted:** {today}  \n"
            f"**Proposal:** [#{number}]({issue_url})  \n"
            f"**Proposed by:** {proposer_display}  \n"
            f"**Vote:** {for_votes} for, {against_votes} against  \n"
            f"{cost_line}"
            f"{signatories_block}\n"
            "\n---\n\n"
            f"{body}\n\n"
            "---\n\n"
            f"*{narrative}*\n",
            encoding="utf-8",
        )

        append_history(law_number, clean_title, number, for_votes, against_votes, True, today)
        update_laws_index(law_number, clean_title, number, issue_url, state["era"], today)
        track_citizen_activity(for_voters, against_voters)
        if proposer:
            track_citizen_proposal(proposer)
        run(["git", "add", "-A"])
        run(["git", "commit", "-m",
             f"[LAW] law-{law_number:03d}: {clean_title} (#{number})"])
        update_proposal_cooldown(effect_data, today)
        apply_tags(effect_data, state_before, state, law_number, clean_title, threshold_tags)

        world_note = ("\n\n**World changes:** " + ", ".join(world_changes)) if world_changes else ""
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body",
             f"**Law {law_number:03d} enacted.** Vote: {for_votes}+1 {against_votes}-1\n\n"
             f"{narrative}{world_note}"])
        run(["gh", "issue", "edit", str(number), "--repo", REPO,
             "--add-label", "passed", "--remove-label", "proposal"])
        run(["gh", "issue", "close", str(number), "--repo", REPO])

    else:
        print(f"  #{number}: REJECTED ({for_votes}+1 {against_votes}-1)")
        stats = read_stats()
        stats["proposals_total"]    = stats.get("proposals_total", 0) + 1
        stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
        write_stats(stats)

        state = read_state()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        generate_dashboard_svg(stats, today)
        generate_map_svg(today)
        generate_world_md(state, None, today)
        update_readme(state, stats, None, today)
        append_history(None, clean_title, number, for_votes, against_votes, False, today)
        run(["git", "add", "-A"])
        run(["git", "commit", "-m", f"[REJECTED] {clean_title} (#{number})"])
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", f"Proposal rejected. Vote: {for_votes}+1 {against_votes}-1"])
        run(["gh", "issue", "edit", str(number), "--repo", REPO,
             "--add-label", "rejected", "--remove-label", "proposal"])
        run(["gh", "issue", "close", str(number), "--repo", REPO])


# ── Citizen signatures ────────────────────────────────────────────────────────

def format_signatories(for_voters: list[str], against_voters: list[str]) -> str:
    total = len(for_voters) + len(against_voters)
    for_str     = (", ".join(f"@{u}" for u in for_voters)     or "—")
    against_str = (", ".join(f"@{u}" for u in against_voters) or "—")
    if total <= SIGNATURE_THRESHOLD:
        return (
            f"**Voted for:** {for_str}  \n"
            f"**Voted against:** {against_str}"
        )
    return (
        f"<details>\n"
        f"<summary>👥 {total} signatories</summary>\n\n"
        f"**For:** {for_str}  \n"
        f"**Against:** {against_str}\n\n"
        f"</details>"
    )


def track_citizen_activity(for_voters: list[str], against_voters: list[str]):
    path = Path("world/citizens.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for user in for_voters + against_voters:
        entry = data.setdefault(user, {"total_votes": 0, "total_proposals": 0, "last_active": now_iso})
        entry["total_votes"] += 1
        entry["last_active"] = now_iso
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def track_citizen_proposal(proposer: str):
    path = Path("world/citizens.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    entry = data.setdefault(proposer, {"total_votes": 0, "total_proposals": 0, "last_active": now_iso})
    entry["total_proposals"] += 1
    entry["last_active"] = now_iso
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ── Proposal cooldown ─────────────────────────────────────────────────────────

def check_proposal_cooldown(effect_data: dict | None) -> tuple[bool, str]:
    if not effect_data or effect_data.get("type") != "policy":
        return True, ""
    path = Path("world/proposal_cooldowns.json")
    if not path.exists():
        return True, ""
    try:
        cooldowns = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, ""
    today = datetime.now(timezone.utc).date()
    for metric in effect_data.get("changes", {}):
        if metric not in cooldowns:
            continue
        try:
            last_date = datetime.fromisoformat(cooldowns[metric]).date()
        except (ValueError, TypeError):
            continue
        if (today - last_date).days < COOLDOWN_DAYS:
            until = (last_date + timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%d")
            return False, f"metric '{metric}' on cooldown until {until}"
    return True, ""


def update_proposal_cooldown(effect_data: dict | None, date: str):
    if not effect_data or effect_data.get("type") != "policy":
        return
    path = Path("world/proposal_cooldowns.json")
    try:
        cooldowns = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        cooldowns = {}
    for metric in effect_data.get("changes", {}):
        cooldowns[metric] = date
    path.write_text(json.dumps(cooldowns, indent=2) + "\n", encoding="utf-8")


# ── Weekly representatives ────────────────────────────────────────────────────

def select_weekly_representatives():
    reps_path = Path("world/representatives.json")
    reps = json.loads(reps_path.read_text(encoding="utf-8")) if reps_path.exists() else {"selected_at": None}
    if reps.get("selected_at"):
        try:
            last = datetime.fromisoformat(reps["selected_at"]).date()
            if (datetime.now(timezone.utc).date() - last).days < REPRESENTATIVE_DAYS:
                return
        except (ValueError, TypeError):
            pass  # malformed date — allow re-selection
    citizens_path = Path("world/citizens.json")
    if not citizens_path.exists():
        return
    try:
        citizens = json.loads(citizens_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not citizens:
        return
    top3 = sorted(citizens.items(), key=lambda x: x[1].get("total_votes", 0), reverse=True)[:3]
    representatives = [u for u, _ in top3]
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    next_str = (datetime.now(timezone.utc) + timedelta(days=REPRESENTATIVE_DAYS)).strftime("%Y-%m-%d")
    reps_path.write_text(
        json.dumps({"selected_at": today_str, "next_selection": next_str,
                    "representatives": representatives}, indent=2) + "\n",
        encoding="utf-8",
    )
    if representatives:
        names = ", ".join(f"@{r}" for r in representatives)
        dispatch_num = get_or_create_dispatch_issue()
        run(["gh", "issue", "comment", str(dispatch_num), "--repo", REPO,
             "--body", f"**Weekly Representatives elected:** {names}\n\nThese citizens showed the highest engagement this cycle."])
    print(f"  Representatives: {representatives}")


# ── World annals ──────────────────────────────────────────────────────────────

def generate_annals(history: list):
    if not history:
        return
    tick_num = history[-1].get("tick", len(history))
    if tick_num % ANNALS_INTERVAL != 0:
        return
    chapter_num = tick_num // ANNALS_INTERVAL
    chapter_path = Path(f"world/annals/chapter-{chapter_num:03d}.md")
    if chapter_path.exists():
        return
    recent = history[-ANNALS_INTERVAL:]
    state = read_state()
    summary_data = {
        "chapter": chapter_num,
        "ticks": f"{tick_num - ANNALS_INTERVAL + 1}–{tick_num}",
        "era": state.get("era"),
        "laws_in_period": recent[-1].get("laws_count", 0) - recent[0].get("laws_count", 0),
        "pop_change": recent[-1].get("population", 0) - recent[0].get("population", 0),
        "treasury_change": recent[-1].get("treasury", 0) - recent[0].get("treasury", 0),
    }
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": (
            "You are the official historian of Gitizens.\n"
            f"Write World Annals Chapter {chapter_num}, covering ticks {summary_data['ticks']}.\n"
            f"Data: {json.dumps(summary_data)}\n"
            f"Current world: {json.dumps(_state_for_llm(state))}\n\n"
            "Format:\n"
            f"# World Annals — Chapter {chapter_num}\n\n"
            "## Summary\n<3-4 sentences of narrative history>\n\n"
            "## Key Events\n<bullet points of notable changes>\n\n"
            "## Citizen Voices\n<2 short quotes from fictional citizens, different perspectives>"
        )}],
        max_tokens=400,
        temperature=0.7,
    )
    content = response.choices[0].message.content.strip()
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(content + "\n", encoding="utf-8")
    tag = f"annals/ch-{chapter_num:03d}"
    run(["gh", "release", "create", tag, "--repo", REPO,
         "--title", f"[Annals] Chapter {chapter_num:03d}",
         "--notes-file", str(chapter_path),
         "--target", "master"])
    print(f"  Annals chapter {chapter_num} published as Release {tag}")


# ── Event chains ──────────────────────────────────────────────────────────────

def fire_chained_event(resolved_event: dict, responded: bool):
    chain_key = "triggers_next_on_response" if responded else "triggers_next_on_default"
    next_evt_id = resolved_event.get(chain_key)
    if not next_evt_id:
        return
    pool = load_event_pool()
    next_evt = next((e for e in pool if e.get("id") == next_evt_id), None)
    if not next_evt or load_active_event():
        return
    print(f"  Event chain: {resolved_event.get('title')} → {next_evt['title']}")
    apply_event_effects(next_evt, "immediate_effects")
    issue_num = open_event_issue(next_evt)
    next_evt = json.loads(json.dumps(next_evt))
    next_evt["fired_at"] = datetime.now(timezone.utc).isoformat()
    next_evt["issue_number"] = issue_num
    next_evt["chained_from"] = resolved_event.get("id")
    save_active_event(next_evt)


# ── Crisis multiplier ─────────────────────────────────────────────────────────

def apply_crisis_multiplier(effect_data: dict | None, active_event: dict) -> dict | None:
    if not effect_data or not active_event.get("is_crisis"):
        return effect_data
    if effect_data.get("type") != "policy":
        return effect_data
    multiplier = active_event.get("crisis_multiplier", 1.5)
    modified = dict(effect_data)
    modified["changes"] = {
        k: int(round(v * multiplier)) for k, v in effect_data.get("changes", {}).items()
    }
    print(f"  Crisis multiplier {multiplier}x applied to policy changes")
    return modified


# ── AI citizen narrator ───────────────────────────────────────────────────────

def generate_citizen_narrator():
    state = read_state()
    last_narrator = state.get("last_narrator_date")
    if last_narrator:
        last_dt = datetime.fromisoformat(last_narrator.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_dt).days < 7:
            return
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": (
            "You are writing citizen perspectives for Gitizens.\n"
            f"World state: {json.dumps(_state_for_llm(state))}\n\n"
            "Write 3 short diary entries (2-3 sentences each) from 3 different fictional citizens:\n"
            "1. A government official\n2. A factory worker\n3. A teacher\n\n"
            "Each entry: '**[Name], [Occupation]:**\\n<diary entry>'\n"
            "Make them react to the current world metrics and recent laws. Tone: vivid, personal. No emoji."
        )}],
        max_tokens=300,
        temperature=0.9,
    )
    narrative = response.choices[0].message.content.strip()
    body = (
        f"## Citizen Voices — {today_str}\n\n"
        f"{narrative}\n\n"
        f"*These are fictional citizen perspectives generated by the world engine.*"
    )
    run(["gh", "label", "create", "citizen-voices", "--repo", REPO,
         "--color", "d93f0b", "--description", "Weekly citizen diary", "--force"])
    run(["gh", "issue", "create", "--repo", REPO,
         "--title", f"[Citizen Voices] {today_str}",
         "--label", "citizen-voices",
         "--body", body])
    state["last_narrator_date"] = datetime.now(timezone.utc).isoformat()
    write_state(state)
    print("  Citizen narrator posted")


def main():
    collect_star_income()
    tick_changed = world_autonomous_tick()

    proposals = get_open_proposals()
    print(f"Open proposals: {len(proposals)}")
    laws_this_tick = 0
    for proposal in proposals:
        laws_before = read_state().get("laws_count", 0)
        process_issue(proposal)
        if read_state().get("laws_count", 0) > laws_before:
            laws_this_tick += 1

    # Process AI proposals (default pass, 👎 vetoes)
    for ai_proposal in get_ai_proposals():
        laws_before = read_state().get("laws_count", 0)
        process_ai_proposal(ai_proposal)
        if read_state().get("laws_count", 0) > laws_before:
            laws_this_tick += 1

    # Process citizen feedbacks (default apply, 👎 dismisses)
    feedbacks_applied = 0
    for feedback in get_feedbacks():
        if process_feedback(feedback):
            feedbacks_applied += 1

    # Check if the active event expired
    active_before = load_active_event()
    resolved_event_title = active_before.get("title", "") if active_before else ""
    event_resolved = check_event_expiry(laws_this_tick)

    # Maybe fire a new event if none is active
    active_event_title = ""
    if not load_active_event():
        state = read_state()
        new_event = fire_random_event(state)
        if new_event:
            active_event_title = new_event["title"]
            print(f"  Firing event: {new_event['title']} ({new_event['rarity']})")
            apply_event_effects(new_event, "immediate_effects")
            issue_num = open_event_issue(new_event)
            new_event["fired_at"] = datetime.now(timezone.utc).isoformat()
            new_event["issue_number"] = issue_num
            save_active_event(new_event)
            print(f"  Event issue #{issue_num} opened")
    else:
        active_event_title = load_active_event().get("title", "")

    # Generate new AI proposals and feedbacks for next cycle
    from auto_propose import should_generate, generate_ai_proposal, generate_feedbacks as gen_feedbacks
    should_prop, should_fb = should_generate(REPO)
    if should_prop:
        generate_ai_proposal(client, read_state(), REPO)
    if should_fb:
        gen_feedbacks(client, read_state(), REPO)

    # Post world dispatch to Chronicle issue
    post_world_dispatch(
        read_state(), tick_changed, laws_this_tick,
        active_event_title, feedbacks_applied,
    )

    # Snapshot world history (do this before annals so tick count is accurate)
    append_history_snapshot(read_state())

    # World annals (every 10 ticks)
    try:
        hist_data = json.loads(Path("world/history.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        hist_data = []
    generate_annals(hist_data)

    # Weekly representatives
    select_weekly_representatives()

    # AI citizen narrator (weekly)
    generate_citizen_narrator()

    # Compute and store next tick time in state
    _now = datetime.now(timezone.utc)
    _next_hour = ((_now.hour // 4) + 1) * 4
    if _next_hour >= 24:
        _next_tick = _now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    else:
        _next_tick = _now.replace(hour=_next_hour, minute=0, second=0, microsecond=0)
    _state = read_state()
    _state["next_tick_at"] = _next_tick.strftime("%Y-%m-%dT%H:%M:%SZ")
    write_state(_state)

    # Commit any uncommitted world/ changes
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dirty = run(["git", "status", "--porcelain", "world/"])
    if dirty:
        stats = read_stats()
        generate_dashboard_svg(stats, today)
        generate_map_svg(today)
        state = read_state()
        generate_world_md(state, None, today)
        update_readme(state, stats, None, today)
        run(["git", "add", "-A"])
        if event_resolved and resolved_event_title:
            commit_msg = f"[EVENT] resolved: {resolved_event_title[:50]}"
        elif event_resolved:
            commit_msg = "[EVENT] event resolved"
        elif tick_changed:
            commit_msg = "[WORLD] autonomous tick"
        else:
            commit_msg = "[WORLD] state update"
        run(["git", "commit", "-m", commit_msg])

    unpushed = run(["git", "log", "origin/master..HEAD", "--oneline"])
    if unpushed:
        run(["git", "push", "origin", "master", "--follow-tags"])
        print("Pushed.")


if __name__ == "__main__":
    main()
