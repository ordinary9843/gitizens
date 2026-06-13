import json
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
            current = current_state.get(metric, 50)
            if current + delta < 5:
                delta = max(5 - current, -50)
            effects[metric] = delta
    return event
