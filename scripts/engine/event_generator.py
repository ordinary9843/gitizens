import json
import random
import re
from pathlib import Path


def parse_llm_output(raw: str) -> dict | None:
    if not raw or not raw.strip():
        return None
    cleaned = raw.strip()
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    if match:
        try:
            result = json.loads(match.group(1))
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            pass
    return None


REQUIRED_FIELDS = {
    "id", "category", "rarity", "title", "description",
    "immediate_effects", "response_consequence", "default_consequence",
    "duration_hours",
}
VALID_RARITIES = {"common", "uncommon", "rare", "legendary"}
VALID_CATEGORIES = {
    "natural", "economic", "health", "security", "scientific",
    "social", "political", "cosmic", "weird",
}


def validate_event(event: dict) -> bool:
    if not isinstance(event, dict):
        return False
    if not REQUIRED_FIELDS.issubset(event.keys()):
        return False
    if event.get("rarity") not in VALID_RARITIES:
        return False
    if event.get("category") not in VALID_CATEGORIES:
        return False
    for key in ("immediate_effects", "response_consequence", "default_consequence"):
        effects = event.get(key)
        if not isinstance(effects, dict):
            return False
        if not all(isinstance(v, (int, float)) for v in effects.values()):
            return False
    # normalize duration_hours in place so callers receive the corrected value
    try:
        duration = float(event["duration_hours"])
        event["duration_hours"] = duration if duration >= 0 else 4.0
    except (TypeError, ValueError):
        event["duration_hours"] = 4.0
    return True


def apply_clamps(event: dict, current_state: dict) -> dict:
    for key in ("immediate_effects", "response_consequence", "default_consequence"):
        effects = event.get(key, {})
        for metric, delta in list(effects.items()):
            delta = max(-50, min(50, int(round(delta))))
            current = current_state.get(metric, 50)  # unknown metrics default to midpoint
            if current + delta < 5:
                delta = max(5 - current, -50)
            effects[metric] = delta
    return event


# ---------------------------------------------------------------------------
# Prompt-building helpers
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a world event generator for a civilization simulation. "
    "Generate a single JSON world event that naturally emerges from the current world state. "
    "The event must be surprising yet plausible given the described conditions. "
    "Return ONLY valid JSON matching the schema exactly - no prose, no markdown."
)

_OUTPUT_SCHEMA = """{
  "id": "evt-llm-<unique_suffix>",
  "category": "<one of: natural|economic|health|security|scientific|social|political|cosmic|weird>",
  "rarity": "<one of: common|uncommon|rare|legendary>",
  "title": "<short evocative title>",
  "description": "<2-3 sentence event description>",
  "flavor": "<optional atmospheric sentence>",
  "immediate_effects": {"<metric>": <integer delta>, ...},
  "response_consequence": {"<metric>": <integer delta>, ...},
  "default_consequence": {"<metric>": <integer delta>, ...},
  "response_hint": "<what passing a relevant law would do>",
  "duration_hours": <number>,
  "chained_from": null
}"""

_RARITY_GUIDE = (
    "Rarity distribution: 60% common, 25% uncommon, 12% rare, 3% legendary. "
    "Legendary events may include wars, plagues, revolutions, alien contact, or catastrophic collapse. "
    "Event intensity should scale with rarity - legendary events can have deltas up to +/-40 on multiple metrics."
)

_TREND_METRICS = ["treasury", "education", "industry", "welfare", "green_policy", "defense", "stability"]


def _build_world_trend(history: list[dict]) -> str:
    """Derive a plain-English trend sentence from the last 6 history snapshots."""
    if not history:
        return "No historical data available."
    recent = history[-6:]
    if len(recent) < 2:
        return "Insufficient history for trend analysis."
    first = recent[0]
    last = recent[-1]
    improving = []
    deteriorating = []
    for metric in _TREND_METRICS:
        first_val = first.get(metric)
        last_val = last.get(metric)
        if first_val is None or last_val is None:
            continue
        delta = last_val - first_val
        if delta > 5:
            improving.append(metric)
        elif delta < -5:
            deteriorating.append(metric)
    parts = []
    if improving:
        parts.append(f"improving: {', '.join(improving)}")
    if deteriorating:
        parts.append(f"deteriorating: {', '.join(deteriorating)}")
    if not parts:
        return "World metrics are stable."
    return "Trend - " + "; ".join(parts) + "."


def _load_recent_laws(n: int = 5) -> list[str]:
    """Return titles of the n most recently enacted laws."""
    try:
        laws_path = Path(__file__).parent.parent.parent / "world" / "laws_index.json"
        if not laws_path.exists():
            return []
        data = json.loads(laws_path.read_text(encoding="utf-8"))
        titles = [entry["title"] for entry in data if "title" in entry]
        return titles[-n:]
    except Exception:
        return []


def _load_recent_event_history(n: int = 3) -> list[str]:
    """Return titles of the n most recent resolved events from annals."""
    try:
        annals_path = Path(__file__).parent.parent.parent / "world" / "annals.json"
        if not annals_path.exists():
            return []
        annals = json.loads(annals_path.read_text(encoding="utf-8"))
        event_entries = [
            entry for entry in annals
            if entry.get("type") == "event"
        ]
        recent = event_entries[-n:]
        return [entry.get("title", "Unknown event") for entry in recent]
    except Exception:
        return []


def _fallback_from_pool(state: dict) -> dict | None:
    """Select a random event from world/event_pool.json, weighted by CATEGORY_MULTIPLIERS."""
    try:
        from .constants import CATEGORY_MULTIPLIERS
        pool_path = Path(__file__).parent.parent.parent / "world" / "event_pool.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        if not pool:
            return None
        weights = []
        for evt in pool:
            cat = evt.get("category", "")
            weight = 1.0
            rules = CATEGORY_MULTIPLIERS.get(cat, [])
            for metric, direction, threshold, multiplier in rules:
                val = state.get(metric, 50)
                if direction == "low" and val < threshold:
                    weight *= multiplier
                elif direction == "high" and val >= threshold:
                    weight *= multiplier
            weights.append(weight)
        return random.choices(pool, weights=weights, k=1)[0]
    except Exception:
        return None


def generate_event(state: dict) -> dict | None:
    """Generate a world event via LLM, falling back to the pool on any failure."""
    try:
        from .content import client
        from .state import read_history
        history = read_history()
        prompt = build_prompt(state, history)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=1.0,
            max_tokens=600,
        )
        raw = response.choices[0].message.content or ""
        event = parse_llm_output(raw)
        if event and validate_event(event):
            return apply_clamps(event, state)
    except Exception:
        pass
    return _fallback_from_pool(state)


def generate_chained_event(resolved_event: dict, responded: bool, state: dict) -> dict | None:
    """Generate a follow-up chained event via LLM after a resolved event."""
    try:
        from .content import client
        from .state import read_history
        outcome = "responded" if responded else "defaulted"
        consequence_key = "response_consequence" if responded else "default_consequence"
        effects = resolved_event.get(consequence_key, {})
        effects_str = ", ".join(f"{k}: {int(v):+d}" for k, v in effects.items()) if effects else "none"
        history = read_history()
        trend = _build_world_trend(history)
        chain_prompt = (
            f"A world event just resolved. Citizens {outcome} to it.\n"
            f"Resolved event: '{resolved_event.get('title', 'Unknown')}'\n"
            f"Outcome effects: {effects_str}\n"
            f"Current world trend: {trend}\n\n"
            f"Generate a NEW follow-up event that naturally emerges from this outcome.\n"
            f"The follow-up must feel causally connected — a direct consequence or ripple effect.\n"
            f"It should have at least 20% chance of being a different category than '{resolved_event.get('category', 'natural')}'.\n\n"
            f"Rarity guide: {_RARITY_GUIDE}\n\n"
            f"Output schema:\n{_OUTPUT_SCHEMA}"
        )
        # guide the LLM to output the correct chained_from value in its schema example
        chain_prompt = chain_prompt.replace(
            '"chained_from": null',
            f'"chained_from": "{resolved_event.get("id", "unknown")}"'
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": chain_prompt},
            ],
            temperature=1.1,
            max_tokens=600,
        )
        raw = response.choices[0].message.content or ""
        event = parse_llm_output(raw)
        if event and validate_event(event):
            event["chained_from"] = resolved_event.get("id", "unknown")
            return apply_clamps(event, state)
    except Exception:
        pass
    return None


def build_prompt(state: dict, history: list[dict]) -> str:
    """Build the user-turn prompt for LLM event generation."""
    metrics_lines = "\n".join(
        f"  {k}: {v}" for k, v in state.items()
        if k in set(_TREND_METRICS) | {"era", "laws_count", "tick_count", "world_summary"}
    )
    trend = _build_world_trend(history)
    recent_laws = _load_recent_laws()
    recent_events = _load_recent_event_history()

    law_text = ", ".join(recent_laws) if recent_laws else "none"
    event_text = ", ".join(recent_events) if recent_events else "none"

    return (
        f"Current world state:\n{metrics_lines}\n\n"
        f"Trend: {trend}\n"
        f"Recent laws enacted: {law_text}\n"
        f"Recent events: {event_text}\n\n"
        f"Rarity guide: {_RARITY_GUIDE}\n\n"
        f"Output schema:\n{_OUTPUT_SCHEMA}"
    )
