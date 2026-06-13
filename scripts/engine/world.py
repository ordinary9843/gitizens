import re
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .constants import (
    POLICY_METRICS, POLICY_COST, _EVOLVE_BLOCKED,
    WORLD_GENERATION_RULES, THRESHOLD_TAGS,
)
from .state import read_json, write_json, read_state, write_state
from .gh import run, SKIP_TIMING


TICK_INTERVAL_HOURS = 2

# Population dynamics — fractional per-tick rates. New population is
# pop + births - deaths + migration + noise, floored at POPULATION_FLOOR.
POPULATION_FLOOR = 100
POPULATION_NOISE_PCT = 0.02  # ±2% uniform jitter per tick


def compute_population_delta(
    pop: int,
    welfare: int,
    pollution: int,
    stability: int,
    defense: int,
    treasury: int,
    rng: random.Random | None = None,
) -> int:
    """Return the new population after one tick of bidirectional dynamics.

    Inputs are clamped at 0 for safety. Result is floored at POPULATION_FLOOR
    so the civilization can never go extinct (gameplay sanity).
    """
    pop = max(0, int(pop))
    welfare    = max(0, int(welfare))
    pollution  = max(0, int(pollution))
    stability  = max(0, int(stability))
    defense    = max(0, int(defense))
    treasury   = max(0, int(treasury))
    rng = rng or random

    birth_rate = 0.010
    if welfare > 60:    birth_rate += 0.005
    if treasury > 1000: birth_rate += 0.005
    if stability > 70:  birth_rate += 0.003

    death_rate = 0.008
    if pollution > 70:  death_rate += 0.010
    elif pollution > 50: death_rate += 0.006
    if welfare < 30:    death_rate += 0.004

    migration_rate = 0.0
    if stability < 30:  migration_rate -= 0.015
    if defense < 30:    migration_rate -= 0.008
    if welfare > 70 and pollution < 30: migration_rate += 0.005

    births    = round(pop * birth_rate)
    deaths    = round(pop * death_rate)
    migration = round(pop * migration_rate)
    noise     = round(pop * rng.uniform(-POPULATION_NOISE_PCT, POPULATION_NOISE_PCT))

    return max(POPULATION_FLOOR, pop + births - deaths + migration + noise)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def compute_next_tick_at(now: datetime) -> str:
    """Return ISO timestamp for the next bot tick.

    Advances `now` by `TICK_INTERVAL_HOURS` and snaps to the top of that hour.
    Cron runs every 2 hours; this guarantees every consecutive run produces a
    different timestamp, so `world/state.json` always becomes dirty on tick.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    target = (now + timedelta(hours=TICK_INTERVAL_HOURS)).replace(
        minute=0, second=0, microsecond=0
    )
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


def pollution_level(state: dict) -> int:
    stored = state.get("pollution")
    if stored is not None:
        return max(0, min(100, stored))
    return max(0, min(100, state.get("industry", 0) - state.get("green_policy", 0)))


def env_bg_color(pollution: int) -> str:
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

    if not SKIP_TIMING:
        next_tick_at = state.get("next_tick_at")
        if next_tick_at:
            try:
                next_dt = datetime.fromisoformat(next_tick_at.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < next_dt:
                    print(f"  Tick skipped — next tick at {next_tick_at}")
                    return False
            except ValueError:
                pass

    ind = state.get("industry", 0)
    grn = state.get("green_policy", 0)
    wel = state.get("welfare", 0)
    dfn = state.get("defense", 0)
    pol = state.get("pollution", 0)
    pop = state.get("population", 1000)
    stb = state.get("stability", 80)
    treasury = state.get("treasury", 0)

    pol_delta = +1 if ind - grn >= 20 else (-1 if grn - ind >= 20 else 0)
    new_pol = max(0, min(100, pol + pol_delta))

    new_pop = compute_population_delta(
        pop=pop, welfare=wel, pollution=new_pol,
        stability=stb, defense=dfn, treasury=treasury,
    )

    target_stb = max(0, min(100, 30 + wel // 5 + dfn // 10 - new_pol // 10))
    new_stb = stb + (1 if stb < target_stb else (-1 if stb > target_stb else 0))
    if wel > 80:
        new_stb = min(100, new_stb + 1)

    industry_income = ind // 10
    pop_income      = new_pop // 500
    new_treasury = min(100_000, treasury + industry_income + pop_income)

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
                state[key] = val
        write_state(state)


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
