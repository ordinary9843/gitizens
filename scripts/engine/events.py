import json
import random
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .constants import RARITY_WEIGHTS
from .gh import run, gh_json, get_reactions, REPO
from .state import load_event_pool, load_active_event, save_active_event
from .world import apply_event_effects

# Per-category weight multipliers driven by world state.
# Format: category -> list of (metric, direction, threshold, multiplier)
# "low"  means  state[metric] < threshold  -> apply multiplier
# "high" means  state[metric] >= threshold -> apply multiplier
CATEGORY_MULTIPLIERS: dict[str, list[tuple[str, str, int | float, float]]] = {
    "natural":    [("green_policy", "low",  40, 2.0), ("green_policy", "high", 70, 0.6)],
    "economic":   [("industry",     "high", 60, 1.5), ("treasury",     "low",  50, 1.4)],
    "health":     [("welfare",      "low",  35, 2.0), ("welfare",      "high", 65, 0.6)],
    "security":   [("defense",      "low",  35, 2.0)],
    "scientific": [("education",    "high", 65, 1.5)],
    "social":     [("welfare",      "low",  40, 1.5), ("stability",    "low",  40, 1.5)],
}


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
    # Apply category-based multipliers so event frequency responds to world state.
    for i, event in enumerate(eligible):
        cat = event.get("category", "")
        for metric, direction, threshold, mult in CATEGORY_MULTIPLIERS.get(cat, []):
            val = state.get(metric, 0)
            if direction == "low" and val < threshold:
                weights[i] *= mult
            elif direction == "high" and val >= threshold:
                weights[i] *= mult
    return random.choices(eligible, weights=weights, k=1)[0]


def open_event_issue(event: dict) -> int:
    run(["gh", "label", "create", "event", "--repo", REPO,
         "--color", "0075ca", "--description", "Active world event", "--force"])
    imm = event.get("immediate_effects", {})
    imm_str = "  ".join(
        f"{k} {'+' if isinstance(v, (int, float)) and v > 0 else ''}{v}"
        for k, v in imm.items() if k != "all_random"
    ) or "none"

    def fmt_effects(d):
        return ", ".join(
            f"{k} {'+' if isinstance(v, (int, float)) and v > 0 else ''}{v}"
            for k, v in d.items() if isinstance(v, (int, float)) and k != "all_random"
        ) or "none"

    body = (
        f"## Random Event: {event['title']}\n\n"
        f"{event['description']}\n\n"
        f"*{event.get('flavor', '')}*\n\n"
        f"---\n\n"
        f"**Category:** {event.get('category', '?').title()}  ·  "
        f"**Rarity:** {event.get('rarity', '?').title()}\n\n"
        f"**Immediate effects (already applied):** {imm_str}\n\n"
        f"**Response window:** 2 hours\n\n"
        f"**👍 React to mobilise** — response: {fmt_effects(event.get('response_consequence', {}))}\n\n"
        f"**👎 React to stand down** — default: {fmt_effects(event.get('default_consequence', {}))}\n\n"
        f"> Hint: {event.get('response_hint', 'React 👍 to trigger the response consequence.')}\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False,
                                    encoding="utf-8") as tf:
        tf.write(body)
        body_path = tf.name
    result = run(["gh", "issue", "create", "--repo", REPO,
                  "--title", f"[EVENT] {event['title']}",
                  "--label", "event",
                  "--body-file", body_path])
    Path(body_path).unlink(missing_ok=True)
    try:
        return int(result.strip().split("/")[-1])
    except (ValueError, IndexError):
        return 0


def close_event_issue(issue_number: int, responded: bool, event: dict):
    if not issue_number:
        return

    def fmt_effects(d):
        return ", ".join(
            f"{k} {'+' if isinstance(v, (int, float)) and v > 0 else ''}{v}"
            for k, v in d.items() if isinstance(v, (int, float)) and k != "all_random"
        ) or "none"

    if responded:
        key = "response_consequence"
        icon = "✅"
        outcome = "Citizens voted 👍 to mobilise. Response consequence applied"
    else:
        key = "default_consequence"
        icon = "Expired"
        outcome = "No citizen response (👎 or no votes). Default consequence applied"
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
    try:
        fired_dt = datetime.fromisoformat(active["fired_at"].replace("Z", "+00:00"))
        if fired_dt.tzinfo is None:
            fired_dt = fired_dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        save_active_event({})
        return False
    now = datetime.now(timezone.utc)
    try:
        duration = float(active.get("duration_hours", 4))
    except (TypeError, ValueError):
        duration = 4.0
    if now < fired_dt + timedelta(hours=duration):
        return False
    event_issue = active.get("issue_number", 0)
    if event_issue:
        for_votes, against_votes, _, _ = get_reactions(event_issue)
        responded = for_votes > against_votes
    else:
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


def fire_chained_event(resolved_event: dict, responded: bool):
    chain_key = "triggers_next_on_response" if responded else "triggers_next_on_default"
    next_evt_id = resolved_event.get(chain_key)
    if not next_evt_id:
        return
    pool = load_event_pool()
    next_evt = next((e for e in pool if e.get("id") == next_evt_id), None)
    if not next_evt or load_active_event():
        return
    print(f"  Event chain: {resolved_event.get('title')} -> {next_evt['title']}")
    apply_event_effects(next_evt, "immediate_effects")
    issue_num = open_event_issue(next_evt)
    next_evt = json.loads(json.dumps(next_evt))
    next_evt["fired_at"] = datetime.now(timezone.utc).isoformat()
    next_evt["issue_number"] = issue_num
    next_evt["chained_from"] = resolved_event.get("id")
    save_active_event(next_evt)


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
