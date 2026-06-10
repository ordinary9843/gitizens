#!/usr/bin/env python3
"""
Tally votes on all open proposal Issues and apply effects.
Called by tally-votes.yml every 6 hours.
"""
import os
import json
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
    "treasury", "currency", "stars_last_counted",
    "education", "industry", "welfare", "green_policy", "defense",
    "population", "pollution", "stability",
    "tags_applied",
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
        "tick":         len(history) + 1,
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

    # Idle income: industrial output + population tax
    industry_income = ind // 10       # 0–8 GC/tick
    pop_income      = new_pop // 500  # 1 GC per 500 citizens
    new_treasury = treasury + industry_income + pop_income

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

def run_world_engine(law_number: int) -> list[str]:
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
        entity_id = effect_data["id"]
        changes = effect_data.get("changes", {})
        for cat in ("buildings", "districts", "institutions", "sectors"):
            path = Path(f"world/entities/{cat}/{entity_id}.json")
            if path.exists():
                entity = read_json(path)
                entity.update(changes)
                entity["last_evolved_law"] = law_number
                write_json(path, entity)
                break

    elif etype == "state_patch":
        patch = effect_data.get("patch", {})
        state = read_state()
        state.update(patch)
        write_state(state)


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

    total_entities = sum(
        read_json(Path(f"world/entities/{cat}/_index.json")).get("count", 0)
        for cat, _ in CATEGORIES
    )

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

    def bar_w(val, max_w=140):
        return max(int(val / 100 * max_w), 2 if val > 0 else 0)

    def mc(val):  # metric color
        if val >= 60: return "#3fb950"
        if val >= 30: return "#e3b341"
        return "#484f58"

    pol_color = "#f85149" if pol >= 60 else "#e3b341" if pol >= 30 else "#3fb950"
    pop_str   = f"{pop:,}" if pop else "—"
    stb_color = "#3fb950" if stb >= 60 else "#e3b341" if stb >= 40 else "#f85149"

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
  <rect x="432" y="158" width="{bar_w(grn)}" height="12" rx="2" fill="{mc(grn)}"/>
  <text x="{432 + bar_w(grn) + 4}" y="168" fill="{mc(grn)}" font-family="monospace" font-size="10">{grn}</text>

  <text x="400" y="186" fill="#8b949e" font-family="monospace" font-size="10">DEF</text>
  <rect x="432" y="176" width="{bar_w(dfn)}" height="12" rx="2" fill="{mc(dfn)}"/>
  <text x="{432 + bar_w(dfn) + 4}" y="186" fill="{mc(dfn)}" font-family="monospace" font-size="10">{dfn}</text>

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
    CHIP_W, CHIP_H, CHIP_GAP = 58, 22, 6

    chips_per_row = (CELL_W - 2 * CELL_PAD + CHIP_GAP) // (CHIP_W + CHIP_GAP)
    chip_rows     = (CELL_H - 38) // (CHIP_H + CHIP_GAP)
    MAX_CHIPS     = chips_per_row * chip_rows

    state = read_state()
    pol = pollution_level(state)
    bg_color = env_bg_color(pol)

    categories_data = []
    total_entities = 0
    for cat, label in CATEGORIES:
        idx = read_json(Path(f"world/entities/{cat}/_index.json"))
        entities = [e for e in idx.get("entities", [])
                    if Path(f"world/entities/{cat}/{e}.json").exists()]
        categories_data.append((cat, label, entities))
        total_entities += len(entities)

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
            show     = entities[:MAX_CHIPS - 1] if count > MAX_CHIPS else entities
            overflow = count - len(show)
            for j, eid in enumerate(show):
                fx = cx + CELL_PAD + (j % chips_per_row) * (CHIP_W + CHIP_GAP)
                fy = cy + 32       + (j // chips_per_row) * (CHIP_H + CHIP_GAP)
                lines += [
                    f'  <rect x="{fx}" y="{fy}" width="{CHIP_W}" height="{CHIP_H}" rx="3" fill="#161b22" stroke="{color}" stroke-width="1"/>',
                    f'  <text x="{fx + CHIP_W // 2}" y="{fy + 15}" fill="#c9d1d9" font-family="monospace" font-size="9" text-anchor="middle">{eid}</text>',
                ]
            if overflow > 0:
                j  = len(show)
                fx = cx + CELL_PAD + (j % chips_per_row) * (CHIP_W + CHIP_GAP)
                fy = cy + 32       + (j // chips_per_row) * (CHIP_H + CHIP_GAP)
                lines += [
                    f'  <rect x="{fx}" y="{fy}" width="{CHIP_W}" height="{CHIP_H}" rx="3" fill="#161b22" stroke="#484f58" stroke-width="1"/>',
                    f'  <text x="{fx + CHIP_W // 2}" y="{fy + 15}" fill="#484f58" font-family="monospace" font-size="9" text-anchor="middle">+{overflow} more</text>',
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
            f"Current state: {json.dumps(state, ensure_ascii=False)}\n\n"
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
        idx = read_json(Path(f"world/entities/{cat}/_index.json"))
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
    archived = sorted(f for f in Path("world/archive").glob("*.json") if f.name != ".gitkeep")
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
    block = f"**Era:** {era} | **Laws enacted:** {laws_count} | [World state](world/WORLD.md)"
    new_content = re.sub(
        r"<!-- STATE_START -->.*?<!-- STATE_END -->",
        f"<!-- STATE_START -->\n{block}\n<!-- STATE_END -->",
        content, flags=re.DOTALL,
    )
    readme_path.write_text(new_content, encoding="utf-8")


def append_history(law_number: int | None, title: str, issue_number: int,
                   for_votes: int, against_votes: int, passed: bool, date: str):
    path = Path("history/INDEX.md")
    content = path.read_text(encoding="utf-8")
    issue_link = f"[#{issue_number}](https://github.com/{REPO}/issues/{issue_number})"
    if passed:
        law_link = f"[law-{law_number:03d}](../world/laws/law-{law_number:03d}.md)"
        row = f"| {law_number} | {law_link} | {issue_link} {title} | {for_votes}+1 {against_votes}-1 | {date} |"
    else:
        row = f"| - | *(rejected)* | {issue_link} {title} | {for_votes}+1 {against_votes}-1 | {date} |"
    path.write_text(content + row + "\n", encoding="utf-8")


# ── Star income ───────────────────────────────────────────────────────────────

def collect_star_income():
    state = read_state()
    if state.get("treasury") is None:
        return
    star_str = run(["gh", "api", f"repos/{REPO}", "--jq", ".stargazers_count"])
    try:
        current_stars = int(star_str)
    except (ValueError, TypeError):
        return
    last_stars = state.get("stars_last_counted")
    state["stars_last_counted"] = current_stars
    if last_stars is None:
        write_state(state)
        print(f"  Star counter initialized at {current_stars}")
        return
    new_stars = max(0, current_stars - last_stars)
    if new_stars == 0:
        return
    income = new_stars * 10
    currency = state.get("currency", "Git Coins")
    state["treasury"] = state.get("treasury", 0) + income
    write_state(state)
    run(["git", "add", "world/state.json"])
    run(["git", "commit", "-m",
         f"[WORLD] treasury: +{income} {currency} from {new_stars} star(s)"])
    print(f"  Star income: +{new_stars} stars → +{income} {currency}")


# ── Issue processing ──────────────────────────────────────────────────────────

def get_open_proposals() -> list:
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "proposal",
        "--state", "open", "--json", "number,title,body,createdAt,author", "--limit", "100",
    ])
    return sorted(issues, key=lambda x: x["number"])


def get_reactions(issue_number: int) -> tuple[int, int, list[str], list[str]]:
    data = gh_json(["api", f"repos/{REPO}/issues/{issue_number}/reactions", "--paginate"])
    user_votes: dict[str, str] = {}
    for r in data:
        user_votes[r["user"]["login"]] = r["content"]
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

        print(f"  #{number}: PASSED ({for_votes}+1 {against_votes}-1) -> law-{law_number:03d}")
        narrative = generate_narrative(clean_title, for_votes, against_votes, state_before)

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

        proposer = issue.get("author", {}).get("login", "unknown")
        for_line     = (", ".join(f"@{u}" for u in for_voters)     or "—")
        against_line = (", ".join(f"@{u}" for u in against_voters) or "—")
        Path(f"world/laws/law-{law_number:03d}.md").write_text(
            f"# Law {law_number:03d}: {clean_title}\n\n"
            f"**Enacted:** {today}  \n"
            f"**Proposal:** [#{number}]({issue_url})  \n"
            f"**Proposed by:** @{proposer}  \n"
            f"**Vote:** {for_votes} for, {against_votes} against  \n"
            f"**Voted for:** {for_line}  \n"
            f"**Voted against:** {against_line}  \n"
            f"{cost_line}"
            "\n---\n\n"
            f"{body}\n\n"
            "---\n\n"
            f"*{narrative}*\n",
            encoding="utf-8",
        )

        append_history(law_number, clean_title, number, for_votes, against_votes, True, today)
        run(["git", "add", "-A"])
        run(["git", "commit", "-m",
             f"[LAW] law-{law_number:03d}: {clean_title} (#{number})"])
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

    # Check if the active event expired
    active_before = load_active_event()
    resolved_event_title = active_before.get("title", "") if active_before else ""
    event_resolved = check_event_expiry(laws_this_tick)

    # Maybe fire a new event if none is active
    if not load_active_event():
        state = read_state()
        new_event = fire_random_event(state)
        if new_event:
            print(f"  Firing event: {new_event['title']} ({new_event['rarity']})")
            apply_event_effects(new_event, "immediate_effects")
            issue_num = open_event_issue(new_event)
            new_event["fired_at"] = datetime.now(timezone.utc).isoformat()
            new_event["issue_number"] = issue_num
            save_active_event(new_event)
            print(f"  Event issue #{issue_num} opened")

    # Snapshot world history
    append_history_snapshot(read_state())

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
