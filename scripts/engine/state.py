import json
from datetime import datetime, timezone
from pathlib import Path


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
