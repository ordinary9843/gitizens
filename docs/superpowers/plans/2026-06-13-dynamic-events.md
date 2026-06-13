# Dynamic Event Generation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed event pool with LLM-generated contextual events; retain pool as fallback for LLM failures.

**Architecture:** New `scripts/engine/event_generator.py` owns all LLM logic. `events.py` keeps the 15% trigger check in `fire_random_event`, calls `generate_event(state)`, and falls back to pool if it returns `None`. Chaining uses `generate_chained_event` instead of pool ID lookup. `_fallback_from_pool` stays in `events.py` to avoid circular imports.

**Tech Stack:** Python 3.10, OpenAI SDK (`engine.content.client`, gpt-4o-mini, GitHub Models), pytest, `unittest.mock`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `scripts/engine/event_generator.py` | **Create** | parse / validate / clamp / prompt / LLM calls |
| `scripts/engine/events.py` | **Modify** | wire `fire_random_event` + `fire_chained_event`; add `_fallback_from_pool` |
| `tests/test_event_generator.py` | **Create** | full test coverage (Groups 1–7) |
| `tests/test_events.py` | **Modify** | rewrite `TestEventChain` for new LLM-based chaining |
| `scripts/engine/__init__.py` | No change | — |

---

### Task 1: Parse + validate + clamp (TDD)

**Files:**
- Create: `scripts/engine/event_generator.py`
- Create: `tests/test_event_generator.py`

- [ ] **Step 1.1 — Create test file with Group 1 (parse) + Group 2 (validate) + Group 3 (clamp)**

Create `tests/test_event_generator.py`:

```python
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("GITHUB_REPOSITORY", "test/repo")
sys.modules.setdefault("openai", MagicMock())

import engine.event_generator as _gen

VALID_EVENT = {
    "id": "evt-llm-test",
    "category": "natural",
    "rarity": "common",
    "title": "Test Event",
    "description": "A test event occurs.",
    "flavor": "Flavor text here.",
    "immediate_effects": {"education": -5},
    "response_consequence": {"treasury": 10},
    "default_consequence": {"stability": -3},
    "response_hint": "Pass a relevant law.",
    "duration_hours": 4,
    "chained_from": None,
}

BASE_STATE = {
    "era": "Founding Era", "laws_count": 8, "tick_count": 10,
    "treasury": 200, "education": 60, "industry": 35,
    "welfare": 70, "green_policy": 70, "defense": 35,
    "pollution": 0, "stability": 79,
    "world_summary": "A stable nation.",
}


# ===========================================================================
# Group 1: parse_llm_output
# ===========================================================================

class TestParseLlmOutput:
    def test_valid_json_string(self):
        result = _gen.parse_llm_output(json.dumps(VALID_EVENT))
        assert result == VALID_EVENT

    def test_json_in_json_code_block(self):
        raw = f"```json\n{json.dumps(VALID_EVENT)}\n```"
        assert _gen.parse_llm_output(raw) == VALID_EVENT

    def test_json_in_plain_code_block(self):
        raw = f"```\n{json.dumps(VALID_EVENT)}\n```"
        assert _gen.parse_llm_output(raw) == VALID_EVENT

    def test_truncated_json_returns_none(self):
        assert _gen.parse_llm_output('{"id": "evt-test", "category"') is None

    def test_pure_prose_returns_none(self):
        assert _gen.parse_llm_output("Here is a world event description.") is None

    def test_empty_string_returns_none(self):
        assert _gen.parse_llm_output("") is None

    def test_whitespace_only_returns_none(self):
        assert _gen.parse_llm_output("   \n  ") is None

    def test_json_array_returns_none(self):
        assert _gen.parse_llm_output(json.dumps([VALID_EVENT])) is None


# ===========================================================================
# Group 2: validate_event
# ===========================================================================

class TestValidateEvent:
    def test_valid_event_passes(self):
        assert _gen.validate_event(dict(VALID_EVENT)) is True

    def test_missing_title_fails(self):
        event = {k: v for k, v in VALID_EVENT.items() if k != "title"}
        assert _gen.validate_event(event) is False

    def test_missing_immediate_effects_fails(self):
        event = {k: v for k, v in VALID_EVENT.items() if k != "immediate_effects"}
        assert _gen.validate_event(event) is False

    def test_missing_response_consequence_fails(self):
        event = {k: v for k, v in VALID_EVENT.items() if k != "response_consequence"}
        assert _gen.validate_event(event) is False

    def test_missing_default_consequence_fails(self):
        event = {k: v for k, v in VALID_EVENT.items() if k != "default_consequence"}
        assert _gen.validate_event(event) is False

    def test_missing_duration_hours_fails(self):
        event = {k: v for k, v in VALID_EVENT.items() if k != "duration_hours"}
        assert _gen.validate_event(event) is False

    def test_invalid_rarity_fails(self):
        assert _gen.validate_event({**VALID_EVENT, "rarity": "mythic"}) is False

    def test_invalid_category_fails(self):
        assert _gen.validate_event({**VALID_EVENT, "category": "military"}) is False

    def test_effect_with_string_value_fails(self):
        assert _gen.validate_event(
            {**VALID_EVENT, "immediate_effects": {"education": "high"}}
        ) is False

    def test_effect_as_list_fails(self):
        assert _gen.validate_event(
            {**VALID_EVENT, "immediate_effects": ["education", -5]}
        ) is False

    def test_duration_hours_as_string_defaults_to_4(self):
        event = {**VALID_EVENT, "duration_hours": "four"}
        assert _gen.validate_event(event) is True
        assert event["duration_hours"] == 4.0

    def test_duration_hours_negative_defaults_to_4(self):
        event = {**VALID_EVENT, "duration_hours": -1}
        assert _gen.validate_event(event) is True
        assert event["duration_hours"] == 4.0

    def test_all_valid_rarities_accepted(self):
        for rarity in ("common", "uncommon", "rare", "legendary"):
            assert _gen.validate_event({**VALID_EVENT, "rarity": rarity}) is True

    def test_all_valid_categories_accepted(self):
        for cat in ("natural", "economic", "health", "security",
                    "scientific", "social", "political", "cosmic", "weird"):
            assert _gen.validate_event({**VALID_EVENT, "category": cat}) is True


# ===========================================================================
# Group 3: apply_clamps
# ===========================================================================

class TestApplyClamps:
    def _evt(self, imm=None, resp=None, dflt=None):
        return {
            **VALID_EVENT,
            "immediate_effects": imm or {},
            "response_consequence": resp or {},
            "default_consequence": dflt or {},
        }

    def test_positive_delta_above_50_clamped(self):
        event = self._evt(imm={"treasury": 80})
        assert _gen.apply_clamps(event, BASE_STATE)["immediate_effects"]["treasury"] == 50

    def test_negative_delta_below_minus50_clamped(self):
        event = self._evt(imm={"treasury": -80})
        assert _gen.apply_clamps(event, BASE_STATE)["immediate_effects"]["treasury"] == -50

    def test_delta_exactly_50_allowed(self):
        event = self._evt(imm={"education": 50})
        assert _gen.apply_clamps(event, BASE_STATE)["immediate_effects"]["education"] == 50

    def test_delta_51_clamped_to_50(self):
        event = self._evt(imm={"education": 51})
        assert _gen.apply_clamps(event, BASE_STATE)["immediate_effects"]["education"] == 50

    def test_delta_zero_unchanged(self):
        event = self._evt(imm={"welfare": 0})
        assert _gen.apply_clamps(event, BASE_STATE)["immediate_effects"]["welfare"] == 0

    def test_floor_clamp_prevents_metric_below_5(self):
        # current=4, delta=-3 → 4+(-3)=1 < 5 → delta becomes 5-4=1
        state = {**BASE_STATE, "education": 4}
        event = self._evt(imm={"education": -3})
        assert _gen.apply_clamps(event, state)["immediate_effects"]["education"] == 1

    def test_metric_at_low_value_large_negative_clamped(self):
        # current=0, delta=-10 → 0+(-10)=-10 < 5 → delta = max(5-0, -50) = 5
        state = {**BASE_STATE, "treasury": 0}
        event = self._evt(imm={"treasury": -10})
        assert _gen.apply_clamps(event, state)["immediate_effects"]["treasury"] == 5

    def test_multiple_metrics_clamped_independently(self):
        # treasury=3: 3+(-10)=-7 < 5 → delta=2
        # education=2: 2+(-5)=-3 < 5 → delta=3
        # welfare=6: 6+(-3)=3 < 5 → delta=5-6=-1
        state = {**BASE_STATE, "treasury": 3, "education": 2, "welfare": 6}
        event = self._evt(imm={"treasury": -10, "education": -5, "welfare": -3})
        result = _gen.apply_clamps(event, state)["immediate_effects"]
        assert result["treasury"] == 2
        assert result["education"] == 3
        assert result["welfare"] == -1

    def test_clamps_applied_to_all_three_effect_keys(self):
        event = self._evt(imm={"treasury": 80}, resp={"treasury": -80}, dflt={"treasury": 100})
        result = _gen.apply_clamps(event, BASE_STATE)
        assert result["immediate_effects"]["treasury"] == 50
        assert result["response_consequence"]["treasury"] == -50
        assert result["default_consequence"]["treasury"] == 50

    def test_unknown_metric_uses_default_50_for_floor(self):
        # unknown_metric not in state → current defaults to 50
        # delta=-80 → after ±50 clamp: -50 → 50+(-50)=0 < 5 ��� delta = max(5-50, -50) = -45
        event = self._evt(imm={"unknown_metric": -80})
        assert _gen.apply_clamps(event, BASE_STATE)["immediate_effects"]["unknown_metric"] == -45

    def test_float_delta_rounded(self):
        event = self._evt(imm={"education": 7.6})
        assert _gen.apply_clamps(event, BASE_STATE)["immediate_effects"]["education"] == 8
```

- [ ] **Step 1.2 — Run tests, confirm failure**

```
python -m pytest tests/test_event_generator.py -v 2>&1 | head -20
```

Expected: `ModuleNotFoundError: No module named 'engine.event_generator'`

- [ ] **Step 1.3 — Create `scripts/engine/event_generator.py` with parse + validate + clamp**

```python
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
        event["duration_hours"] = duration if duration > 0 else 4.0
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
```

- [ ] **Step 1.4 — Run all three groups, confirm pass**

```
python -m pytest tests/test_event_generator.py -v
```

Expected: 31 PASSED.

- [ ] **Step 1.5 — Commit**

```
git add scripts/engine/event_generator.py tests/test_event_generator.py
git commit -m "feat: add event_generator parse, validate, clamp"
```

---

### Task 2: Prompt-building helpers (TDD)

**Files:**
- Modify: `scripts/engine/event_generator.py` (append)
- Modify: `tests/test_event_generator.py` (append Group for prompts)

- [ ] **Step 2.1 — Add prompt tests to `tests/test_event_generator.py`**

Append:

```python
# ===========================================================================
# Group: build_prompt + _build_world_trend
# ===========================================================================

class TestBuildPrompt:
    LAWS = ["Open Source Act", "Green Energy Mandate"]
    EVENTS = ["Great Drought", "Tech Boom"]

    def test_prompt_contains_era(self):
        p = _gen.build_prompt(BASE_STATE, self.LAWS, self.EVENTS, "stable")
        assert "Founding Era" in p

    def test_prompt_contains_all_metrics(self):
        p = _gen.build_prompt(BASE_STATE, self.LAWS, self.EVENTS, "stable")
        for m in ("treasury", "education", "industry", "welfare",
                  "green_policy", "defense", "pollution", "stability"):
            assert m in p

    def test_prompt_contains_laws(self):
        p = _gen.build_prompt(BASE_STATE, self.LAWS, self.EVENTS, "stable")
        assert "Open Source Act" in p
        assert "Green Energy Mandate" in p

    def test_prompt_contains_recent_events(self):
        p = _gen.build_prompt(BASE_STATE, self.LAWS, self.EVENTS, "stable")
        assert "Great Drought" in p

    def test_prompt_contains_trend(self):
        p = _gen.build_prompt(BASE_STATE, self.LAWS, self.EVENTS, "deteriorating")
        assert "deteriorating" in p

    def test_prompt_contains_world_summary(self):
        p = _gen.build_prompt(BASE_STATE, self.LAWS, self.EVENTS, "stable")
        assert "A stable nation." in p

    def test_prompt_with_empty_laws(self):
        p = _gen.build_prompt(BASE_STATE, [], [], "stable")
        assert "none" in p

    def test_prompt_truncates_laws_to_10(self):
        many_laws = [f"Law {i}" for i in range(15)]
        p = _gen.build_prompt(BASE_STATE, many_laws, [], "stable")
        assert "Law 10" not in p
        assert "Law 9" in p


class TestBuildWorldTrend:
    def _snap(self, val):
        return {m: str(val) for m in
                ("education", "industry", "welfare", "green_policy",
                 "defense", "stability", "treasury")}

    def test_stable_when_less_than_6_snapshots(self):
        assert _gen._build_world_trend([self._snap(50)] * 3) == "stable"

    def test_improving_when_recent_avg_much_higher(self):
        previous = [self._snap(40)] * 3
        recent = [self._snap(55)] * 3
        assert _gen._build_world_trend(previous + recent) == "improving"

    def test_deteriorating_when_recent_avg_much_lower(self):
        previous = [self._snap(60)] * 3
        recent = [self._snap(40)] * 3
        assert _gen._build_world_trend(previous + recent) == "deteriorating"

    def test_stable_when_delta_within_5(self):
        previous = [self._snap(50)] * 3
        recent = [self._snap(53)] * 3
        assert _gen._build_world_trend(previous + recent) == "stable"

    def test_handles_empty_history(self):
        assert _gen._build_world_trend([]) == "stable"

    def test_handles_non_numeric_values(self):
        snap = {m: "n/a" for m in ("education", "industry", "welfare",
                                   "green_policy", "defense", "stability", "treasury")}
        assert _gen._build_world_trend([snap] * 6) == "stable"
```

- [ ] **Step 2.2 — Run new tests, confirm failure**

```
python -m pytest tests/test_event_generator.py::TestBuildPrompt tests/test_event_generator.py::TestBuildWorldTrend -v 2>&1 | head -10
```

Expected: `AttributeError: module 'engine.event_generator' has no attribute 'build_prompt'`

- [ ] **Step 2.3 — Append to `scripts/engine/event_generator.py`**

```python
_TREND_METRICS = (
    "education", "industry", "welfare", "green_policy",
    "defense", "stability", "treasury",
)

_SYSTEM_PROMPT = (
    "You are a world event generator for a GitHub-native nation simulation.\n"
    "Generate a single world event as a JSON object. The event must feel like a\n"
    "natural consequence of the current world state. Extreme events (war, famine,\n"
    "revolution, collapse) are allowed and encouraged when the state warrants it.\n"
    "The world must always have a path to recovery - no single event may push\n"
    "all metrics below 5 simultaneously."
)

_OUTPUT_SCHEMA = """{
  "id": "evt-llm-<slug>",
  "category": "<natural|economic|health|security|scientific|social|political|cosmic|weird>",
  "rarity": "<common|uncommon|rare|legendary>",
  "title": "<short dramatic title>",
  "description": "<2-3 sentences>",
  "flavor": "<1 sentence atmospheric detail>",
  "immediate_effects": {"<metric>": <int>},
  "response_consequence": {"<metric>": <int>},
  "default_consequence": {"<metric>": <int>},
  "response_hint": "<what law would help>",
  "duration_hours": 4,
  "chained_from": null
}"""

_RARITY_GUIDE = (
    "Rarity effect magnitude guide:\n"
    "- common: +-3-10 per metric, 1-2 metrics\n"
    "- uncommon: +-8-20 per metric, 2-3 metrics\n"
    "- rare: +-15-35 per metric, 3-4 metrics\n"
    "- legendary: +-25-60 per metric, 4+ metrics, duration 24-72h"
)


def _build_world_trend(history: list) -> str:
    if len(history) < 6:
        return "stable"
    recent = history[-3:]
    previous = history[-6:-3]

    def _avg(snapshots):
        vals = []
        for snap in snapshots:
            for m in _TREND_METRICS:
                try:
                    vals.append(float(snap.get(m, 0)))
                except (TypeError, ValueError):
                    pass
        return sum(vals) / len(vals) if vals else 0.0

    delta = _avg(recent) - _avg(previous)
    if delta > 5:
        return "improving"
    if delta < -5:
        return "deteriorating"
    return "stable"


def _load_recent_laws(n: int = 10) -> list:
    laws_path = Path("world/laws_index.json")
    if not laws_path.exists():
        return []
    try:
        laws = json.loads(laws_path.read_text(encoding="utf-8"))
        sorted_laws = sorted(laws, key=lambda x: x.get("enacted_date", ""), reverse=True)
        return [law["title"] for law in sorted_laws[:n] if law.get("title")]
    except (json.JSONDecodeError, OSError):
        return []


def _load_recent_event_history(n: int = 5) -> list:
    hist_path = Path("world/history.json")
    if not hist_path.exists():
        return []
    try:
        history = json.loads(hist_path.read_text(encoding="utf-8"))
        seen: set = set()
        titles = []
        for snap in reversed(history):
            title = snap.get("active_event", "")
            if title and title != "None" and title not in seen:
                titles.append(title)
                seen.add(title)
                if len(titles) >= n:
                    break
        return titles
    except (json.JSONDecodeError, OSError):
        return []


def build_prompt(state: dict, laws: list, recent_events: list, trend: str) -> str:
    metrics = {k: state.get(k, 0) for k in
               ("treasury", "education", "industry", "welfare",
                "green_policy", "defense", "pollution", "stability")}
    metrics_str = ", ".join(f"{k}={v}" for k, v in metrics.items())
    laws_str = "; ".join(laws[:10]) if laws else "none"
    events_str = "; ".join(recent_events[:5]) if recent_events else "none"
    return (
        f"Current world state:\n"
        f"- Era: {state.get('era', 'Unknown')}, "
        f"Tick: {state.get('tick_count', 0)}, "
        f"Laws enacted: {state.get('laws_count', 0)}\n"
        f"- Metrics: {metrics_str}\n"
        f"- Active laws: {laws_str}\n"
        f"- Recent events: {events_str}\n"
        f"- World trend: {trend}\n"
        f"- World summary: {state.get('world_summary', 'No summary available.')}\n\n"
        f"Generate a JSON event with this exact schema:\n{_OUTPUT_SCHEMA}\n\n"
        f"{_RARITY_GUIDE}\n\n"
        f"Respond with ONLY the JSON object, no explanation."
    )
```

- [ ] **Step 2.4 — Run prompt tests, confirm pass**

```
python -m pytest tests/test_event_generator.py::TestBuildPrompt tests/test_event_generator.py::TestBuildWorldTrend -v
```

Expected: 14 PASSED.

- [ ] **Step 2.5 — Run full suite so far**

```
python -m pytest tests/test_event_generator.py -v
```

Expected: 45 PASSED.

- [ ] **Step 2.6 — Commit**

```
git add scripts/engine/event_generator.py tests/test_event_generator.py
git commit -m "feat: add event_generator prompt building helpers"
```

---

### Task 3: `generate_event()` — LLM call + fallback (TDD)

**Files:**
- Modify: `scripts/engine/event_generator.py` (append)
- Modify: `tests/test_event_generator.py` (append Groups 4 + 6)

- [ ] **Step 3.1 — Add Group 4 (fallback routing) + Group 6 (trigger probability) tests**

Append to `tests/test_event_generator.py`:

```python
# ===========================================================================
# Group 4: fallback routing  |  Group 6: trigger probability
# ===========================================================================

class TestGenerateEventFallback:
    POOL = [
        {"id": "evt-pool-fallback", "rarity": "common", "category": "natural",
         "title": "Pool Event", "description": "From pool.", "flavor": "Pool.",
         "trigger_conditions": {}, "immediate_effects": {}, "duration_hours": 4,
         "default_consequence": {}, "response_consequence": {}, "response_hint": ""},
    ]

    def _mock_client_response(self, content: str):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = content
        return mock_resp

    def test_llm_exception_triggers_fallback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]), \
             patch("engine.event_generator._fallback_from_pool", return_value=self.POOL[0]) as mock_fb:
            mock_client.chat.completions.create.side_effect = Exception("API error")
            result = _gen.generate_event(BASE_STATE)
        mock_fb.assert_called_once_with(BASE_STATE)
        assert result == self.POOL[0]

    def test_llm_empty_response_triggers_fallback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]), \
             patch("engine.event_generator._fallback_from_pool", return_value=self.POOL[0]) as mock_fb:
            mock_client.chat.completions.create.return_value = self._mock_client_response("")
            result = _gen.generate_event(BASE_STATE)
        mock_fb.assert_called_once()

    def test_llm_invalid_json_triggers_fallback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]), \
             patch("engine.event_generator._fallback_from_pool", return_value=self.POOL[0]) as mock_fb:
            mock_client.chat.completions.create.return_value = self._mock_client_response("{not json}")
            result = _gen.generate_event(BASE_STATE)
        mock_fb.assert_called_once()

    def test_llm_schema_invalid_triggers_fallback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        bad_event = {**VALID_EVENT, "rarity": "mythic"}
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]), \
             patch("engine.event_generator._fallback_from_pool", return_value=self.POOL[0]) as mock_fb:
            mock_client.chat.completions.create.return_value = self._mock_client_response(
                json.dumps(bad_event)
            )
            _gen.generate_event(BASE_STATE)
        mock_fb.assert_called_once()

    def test_fallback_message_printed_on_exception(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]), \
             patch("engine.event_generator._fallback_from_pool", return_value=None):
            mock_client.chat.completions.create.side_effect = Exception("timeout")
            _gen.generate_event(BASE_STATE)
        out = capsys.readouterr().out
        assert "[EVENT] LLM generation failed" in out

    def test_fallback_message_printed_on_invalid_output(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]), \
             patch("engine.event_generator._fallback_from_pool", return_value=None):
            mock_client.chat.completions.create.return_value = self._mock_client_response("")
            _gen.generate_event(BASE_STATE)
        out = capsys.readouterr().out
        assert "[EVENT] LLM generation failed" in out

    def test_valid_llm_output_returns_clamped_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        oversized = {**VALID_EVENT, "immediate_effects": {"treasury": 80}}
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.return_value = self._mock_client_response(
                json.dumps(oversized)
            )
            result = _gen.generate_event(BASE_STATE)
        assert result is not None
        assert result["immediate_effects"]["treasury"] == 50  # clamped

    def test_empty_fallback_pool_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]), \
             patch("engine.event_generator._fallback_from_pool", return_value=None):
            mock_client.chat.completions.create.side_effect = Exception("error")
            result = _gen.generate_event(BASE_STATE)
        assert result is None


class TestGenerateEventTriggerProbability:
    def test_random_014_allows_generation(self):
        with patch("engine.events.random.random", return_value=0.14):
            with patch("engine.event_generator.generate_event") as mock_gen:
                mock_gen.return_value = VALID_EVENT
                import engine.events as ev
                ev.fire_random_event(BASE_STATE)
            mock_gen.assert_called_once()

    def test_random_016_skips_generation(self):
        with patch("engine.events.random.random", return_value=0.16):
            with patch("engine.event_generator.generate_event") as mock_gen:
                import engine.events as ev
                result = ev.fire_random_event(BASE_STATE)
            mock_gen.assert_not_called()
            assert result is None

    def test_random_015_allows_generation(self):
        # 0.15 > 0.15 is False, so generation proceeds
        with patch("engine.events.random.random", return_value=0.15):
            with patch("engine.event_generator.generate_event") as mock_gen:
                mock_gen.return_value = VALID_EVENT
                import engine.events as ev
                ev.fire_random_event(BASE_STATE)
            mock_gen.assert_called_once()

    def test_random_000_allows_generation(self):
        with patch("engine.events.random.random", return_value=0.0):
            with patch("engine.event_generator.generate_event") as mock_gen:
                mock_gen.return_value = VALID_EVENT
                import engine.events as ev
                ev.fire_random_event(BASE_STATE)
            mock_gen.assert_called_once()
```

- [ ] **Step 3.2 — Run new tests, confirm failure**

```
python -m pytest tests/test_event_generator.py::TestGenerateEventFallback tests/test_event_generator.py::TestGenerateEventTriggerProbability -v 2>&1 | head -15
```

Expected: `AttributeError: module 'engine.event_generator' has no attribute 'generate_event'`

- [ ] **Step 3.3 — Append `generate_event` + `_fallback_from_pool` to `event_generator.py`**

```python
from .content import client
from .state import load_event_pool
from .constants import RARITY_WEIGHTS
from .events import CATEGORY_MULTIPLIERS
import random as _random


def _fallback_from_pool(state: dict) -> dict | None:
    pool = load_event_pool()
    if not pool:
        return None
    edu = state.get("education", 0)
    edu_bonus = 5 if edu > 70 else 0
    eligible = [
        e for e in pool
        if all(
            state.get(f, 0) >= r.get("min", 0) and state.get(f, 0) <= r.get("max", 999)
            for f, r in e.get("trigger_conditions", {}).items()
        )
    ]
    if not eligible:
        return None
    weights = [
        RARITY_WEIGHTS.get(e.get("rarity", "common"), 60) +
        (edu_bonus if e.get("rarity") in ("rare", "legendary") else 0)
        for e in eligible
    ]
    for i, event in enumerate(eligible):
        cat = event.get("category", "")
        for metric, direction, threshold, mult in CATEGORY_MULTIPLIERS.get(cat, []):
            val = state.get(metric, 0)
            if direction == "low" and val < threshold:
                weights[i] *= mult
            elif direction == "high" and val >= threshold:
                weights[i] *= mult
    return _random.choices(eligible, weights=weights, k=1)[0]


def generate_event(state: dict) -> dict | None:
    hist_path = Path("world/history.json")
    history = []
    if hist_path.exists():
        try:
            history = json.loads(hist_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    laws = _load_recent_laws()
    recent_events = _load_recent_event_history()
    trend = _build_world_trend(history)
    prompt = build_prompt(state, laws, recent_events, trend)
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
            temperature=1.0,
        )
        raw = response.choices[0].message.content.strip()
        event = parse_llm_output(raw)
        if event is None or not validate_event(event):
            print("[EVENT] LLM generation failed, using pool fallback")
            return _fallback_from_pool(state)
        return apply_clamps(event, state)
    except Exception as e:
        print(f"[EVENT] LLM generation failed: {e}, using pool fallback")
        return _fallback_from_pool(state)
```

**Note:** This import `from .events import CATEGORY_MULTIPLIERS` would create a circular import because `events.py` will import `event_generator.py`. To break the cycle, move `CATEGORY_MULTIPLIERS` out of `events.py` into `constants.py` instead:

In `scripts/engine/constants.py`, append:

```python
CATEGORY_MULTIPLIERS: dict = {
    "natural":    [("green_policy", "low",  40, 2.0), ("green_policy", "high", 70, 0.6)],
    "economic":   [("industry",     "high", 60, 1.5), ("treasury",     "low",  50, 1.4)],
    "health":     [("welfare",      "low",  35, 2.0), ("welfare",      "high", 65, 0.6)],
    "security":   [("defense",      "low",  35, 2.0)],
    "scientific": [("education",    "high", 65, 1.5)],
    "social":     [("welfare",      "low",  40, 1.5), ("stability",    "low",  40, 1.5)],
}
```

In `scripts/engine/events.py`, change:

```python
# Remove the CATEGORY_MULTIPLIERS dict definition
# Add to imports:
from .constants import RARITY_WEIGHTS, CATEGORY_MULTIPLIERS
```

In `scripts/engine/event_generator.py`, use:

```python
from .constants import RARITY_WEIGHTS, CATEGORY_MULTIPLIERS
```

- [ ] **Step 3.4 — Run Group 4 + Group 6, confirm pass**

```
python -m pytest tests/test_event_generator.py::TestGenerateEventFallback tests/test_event_generator.py::TestGenerateEventTriggerProbability -v
```

Expected: 12 PASSED.

- [ ] **Step 3.5 — Confirm existing test_events.py still passes**

```
python -m pytest tests/test_events.py -v
```

Expected: all PASSED. If failures appear due to `CATEGORY_MULTIPLIERS` patch location, update patches from `engine.events.CATEGORY_MULTIPLIERS` to `engine.constants.CATEGORY_MULTIPLIERS`.

- [ ] **Step 3.6 — Commit**

```
git add scripts/engine/event_generator.py scripts/engine/constants.py scripts/engine/events.py tests/test_event_generator.py
git commit -m "feat: add generate_event with LLM call and pool fallback"
```

---

### Task 4: `generate_chained_event()` (TDD)

**Files:**
- Modify: `scripts/engine/event_generator.py` (append)
- Modify: `tests/test_event_generator.py` (append Group 5)

- [ ] **Step 4.1 �� Add Group 5 chain event tests**

Append to `tests/test_event_generator.py`:

```python
# ===========================================================================
# Group 5: generate_chained_event
# ===========================================================================

class TestGenerateChainedEvent:
    RESOLVED = {
        "id": "evt-drought",
        "title": "Great Drought",
        "response_consequence": {"treasury": 20},
        "default_consequence": {"stability": -5},
    }

    def _mock_response(self, content: str):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = content
        return mock_resp

    def test_prompt_contains_responded_when_true(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        captured = []
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.side_effect = (
                lambda **kw: captured.append(kw) or (_ for _ in ()).throw(Exception("stop"))
            )
            _gen.generate_chained_event(self.RESOLVED, True, BASE_STATE)
        prompt_text = str(captured[0]["messages"])
        assert "responded to" in prompt_text

    def test_prompt_contains_did_not_respond_when_false(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        captured = []
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.side_effect = (
                lambda **kw: captured.append(kw) or (_ for _ in ()).throw(Exception("stop"))
            )
            _gen.generate_chained_event(self.RESOLVED, False, BASE_STATE)
        prompt_text = str(captured[0]["messages"])
        assert "did not respond to" in prompt_text

    def test_chained_from_set_to_resolved_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        chain_event = {**VALID_EVENT, "id": "evt-llm-followup", "chained_from": None}
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.return_value = self._mock_response(
                json.dumps(chain_event)
            )
            result = _gen.generate_chained_event(self.RESOLVED, True, BASE_STATE)
        assert result is not None
        assert result["chained_from"] == "evt-drought"

    def test_llm_failure_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.side_effect = Exception("timeout")
            result = _gen.generate_chained_event(self.RESOLVED, True, BASE_STATE)
        assert result is None

    def test_invalid_llm_output_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.return_value = self._mock_response("not json")
            result = _gen.generate_chained_event(self.RESOLVED, True, BASE_STATE)
        assert result is None

    def test_chain_failure_prints_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        with patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.side_effect = Exception("error")
            _gen.generate_chained_event(self.RESOLVED, True, BASE_STATE)
        assert "[EVENT] Chain event LLM generation failed" in capsys.readouterr().out
```

- [ ] **Step 4.2 — Run Group 5, confirm failure**

```
python -m pytest tests/test_event_generator.py::TestGenerateChainedEvent -v 2>&1 | head -10
```

Expected: `AttributeError: module 'engine.event_generator' has no attribute 'generate_chained_event'`

- [ ] **Step 4.3 — Append `generate_chained_event` to `event_generator.py`**

```python
def generate_chained_event(resolved_event: dict, responded: bool, state: dict) -> dict | None:
    hist_path = Path("world/history.json")
    history = []
    if hist_path.exists():
        try:
            history = json.loads(hist_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    laws = _load_recent_laws()
    recent_events = _load_recent_event_history()
    trend = _build_world_trend(history)
    base_prompt = build_prompt(state, laws, recent_events, trend)
    outcome = "responded to" if responded else "did not respond to"
    consequence_key = "response_consequence" if responded else "default_consequence"
    consequence = resolved_event.get(consequence_key, {})
    chain_suffix = (
        f"\nThe previous event \"{resolved_event.get('title', '')}\" just ended.\n"
        f"Citizens {outcome} the event.\n"
        f"The consequence applied was: {json.dumps(consequence)}.\n"
        f"Generate the natural follow-up event that emerges from this outcome.\n"
        f"Set \"chained_from\" to \"{resolved_event.get('id', '')}\"."
    )
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": base_prompt + chain_suffix},
            ],
            max_tokens=500,
            temperature=1.0,
        )
        raw = response.choices[0].message.content.strip()
        event = parse_llm_output(raw)
        if event is None or not validate_event(event):
            print("[EVENT] Chain event LLM generation failed, skipping chain")
            return None
        event["chained_from"] = resolved_event.get("id")
        return apply_clamps(event, state)
    except Exception as e:
        print(f"[EVENT] Chain event LLM generation failed: {e}, skipping chain")
        return None
```

- [ ] **Step 4.4 — Run Group 5, confirm pass**

```
python -m pytest tests/test_event_generator.py::TestGenerateChainedEvent -v
```

Expected: 6 PASSED.

- [ ] **Step 4.5 — Run full test_event_generator.py**

```
python -m pytest tests/test_event_generator.py -v
```

Expected: all PASSED.

- [ ] **Step 4.6 — Commit**

```
git add scripts/engine/event_generator.py tests/test_event_generator.py
git commit -m "feat: add generate_chained_event"
```

---

### Task 5: Update `events.py` + rewrite `TestEventChain` + integration tests

**Files:**
- Modify: `scripts/engine/events.py`
- Modify: `tests/test_events.py` (rewrite `TestEventChain`)
- Modify: `tests/test_event_generator.py` (append Group 7)

- [ ] **Step 5.1 — Rewrite `TestEventChain` in `tests/test_events.py`**

Replace the existing `TestEventChain` class (lines ~184–245) with:

```python
class TestEventChain:
    CHAIN_RESULT = {
        "id": "evt-llm-followup", "category": "natural", "rarity": "common",
        "title": "Recovery", "description": "Recovery begins.", "flavor": "Hope.",
        "immediate_effects": {}, "response_consequence": {}, "default_consequence": {},
        "response_hint": "Pass a law.", "duration_hours": 4, "chained_from": "evt-drought",
    }

    def test_chain_fires_when_llm_returns_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/active_event.json").write_text("{}")
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        resolved = {"id": "evt-drought", "title": "Drought",
                    "response_consequence": {}, "default_consequence": {}}
        with patch.object(_engine_events, "open_event_issue", return_value=99), \
             patch.object(_engine_events, "apply_event_effects"), \
             patch("engine.event_generator.generate_chained_event",
                   return_value=self.CHAIN_RESULT):
            tv.fire_chained_event(resolved, responded=True)
        active = json.loads((tmp_path / "world/active_event.json").read_text())
        assert active.get("id") == "evt-llm-followup"
        assert active.get("chained_from") == "evt-drought"
        assert active.get("issue_number") == 99

    def test_chain_skipped_when_llm_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/active_event.json").write_text("{}")
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        resolved = {"id": "evt-drought", "title": "Drought",
                    "response_consequence": {}, "default_consequence": {}}
        with patch("engine.event_generator.generate_chained_event", return_value=None), \
             patch.object(_engine_events, "open_event_issue") as mock_open:
            tv.fire_chained_event(resolved, responded=True)
        mock_open.assert_not_called()
        active = json.loads((tmp_path / "world/active_event.json").read_text())
        assert active == {}

    def test_no_chain_when_event_already_active(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        existing = {"id": "evt-existing", "fired_at": "2026-01-01T00:00:00+00:00"}
        (tmp_path / "world/active_event.json").write_text(json.dumps(existing))
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        resolved = {"id": "evt-drought", "title": "Drought",
                    "response_consequence": {}, "default_consequence": {}}
        with patch("engine.event_generator.generate_chained_event") as mock_gen:
            tv.fire_chained_event(resolved, responded=True)
        mock_gen.assert_not_called()

    def test_chain_response_true_passed_to_generator(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/active_event.json").write_text("{}")
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        resolved = {"id": "evt-x", "title": "X",
                    "response_consequence": {}, "default_consequence": {}}
        with patch("engine.event_generator.generate_chained_event",
                   return_value=None) as mock_gen:
            tv.fire_chained_event(resolved, responded=False)
        mock_gen.assert_called_once()
        _, responded_arg, _ = mock_gen.call_args[0]
        assert responded_arg is False
```

- [ ] **Step 5.2 — Update `scripts/engine/events.py`**

In `events.py`, make these changes:

**a) Update the top imports** — add `read_state` and remove the `CATEGORY_MULTIPLIERS` definition (it moved to constants):

```python
# Change this import:
from .state import load_event_pool, load_active_event, save_active_event
# To:
from .state import load_event_pool, load_active_event, save_active_event, read_state
```

**b) Remove the `CATEGORY_MULTIPLIERS` dict** (lines 16–23 in the original) — it now lives in `constants.py`. Add it to the constants import:

```python
# Change:
from .constants import RARITY_WEIGHTS
# To:
from .constants import RARITY_WEIGHTS, CATEGORY_MULTIPLIERS
```

**c) Replace `fire_random_event` with:**

`_fallback_from_pool` now lives in `event_generator.py` (added in Task 3) and is called internally by `generate_event`. `fire_random_event` simply delegates:

```python
def fire_random_event(state: dict) -> dict | None:
    if random.random() > 0.15:
        return None
    from .event_generator import generate_event
    return generate_event(state)
```

**d) Replace `fire_chained_event` with:**

```python
def fire_chained_event(resolved_event: dict, responded: bool):
    if load_active_event():
        return
    from .event_generator import generate_chained_event
    state = read_state()
    next_evt = generate_chained_event(resolved_event, responded, state)
    if not next_evt:
        return
    print(f"  Event chain: {resolved_event.get('title')} -> {next_evt.get('title')}")
    apply_event_effects(next_evt, "immediate_effects")
    issue_num = open_event_issue(next_evt)
    next_evt = json.loads(json.dumps(next_evt))
    next_evt["fired_at"] = datetime.now(timezone.utc).isoformat()
    next_evt["issue_number"] = issue_num
    save_active_event(next_evt)
```

- [ ] **Step 5.3 — Run `test_events.py` to confirm rewritten tests pass**

```
python -m pytest tests/test_events.py -v
```

Expected: all PASSED. If any pre-existing tests fail due to `CATEGORY_MULTIPLIERS` now in constants, update patches from `engine.events` to `engine.constants`.

- [ ] **Step 5.4 — Add Group 7 integration tests to `test_event_generator.py`**

Append:

```python
# ===========================================================================
# Group 7: integration (end-to-end with mocked LLM)
# ===========================================================================

class TestIntegration:
    def _mock_response(self, content: str):
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = content
        return mock_resp

    def test_valid_llm_output_stored_in_active_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/active_event.json").write_text("{}")
        (tmp_path / "world/event_pool.json").write_text("[]")
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        import engine.events as ev
        with patch("engine.events.random.random", return_value=0.05), \
             patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.return_value = self._mock_response(
                json.dumps(VALID_EVENT)
            )
            result = ev.fire_random_event(BASE_STATE)
        assert result is not None
        assert result["id"] == VALID_EVENT["id"]

    def test_oversized_effects_clamped_before_return(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        import engine.events as ev
        oversized = {**VALID_EVENT, "immediate_effects": {"treasury": 200}}
        with patch("engine.events.random.random", return_value=0.05), \
             patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.return_value = self._mock_response(
                json.dumps(oversized)
            )
            result = ev.fire_random_event(BASE_STATE)
        assert result["immediate_effects"]["treasury"] == 50

    def test_llm_failure_falls_back_to_pool(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        pool_event = {
            "id": "evt-pool-only", "rarity": "common", "category": "natural",
            "title": "Pool Event", "description": ".", "flavor": ".",
            "trigger_conditions": {}, "immediate_effects": {}, "duration_hours": 4,
            "default_consequence": {}, "response_consequence": {}, "response_hint": "",
        }
        (tmp_path / "world/event_pool.json").write_text(json.dumps([pool_event]))
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        import engine.events as ev
        with patch("engine.events.random.random", return_value=0.05), \
             patch("engine.events.random.choices", return_value=[pool_event]), \
             patch("engine.event_generator.client") as mock_client, \
             patch("engine.event_generator._load_recent_laws", return_value=[]), \
             patch("engine.event_generator._load_recent_event_history", return_value=[]):
            mock_client.chat.completions.create.side_effect = Exception("API down")
            result = ev.fire_random_event(BASE_STATE)
        assert result is not None
        assert result["id"] == "evt-pool-only"

    def test_full_tick_fire_and_expiry(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone, timedelta
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/event_pool.json").write_text("[]")
        (tmp_path / "world/laws_index.json").write_text("[]")
        (tmp_path / "world/history.json").write_text("[]")
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        fired_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        active = {**VALID_EVENT, "fired_at": fired_at, "issue_number": 7}
        (tmp_path / "world/active_event.json").write_text(json.dumps(active))
        import engine.events as ev
        with patch.object(ev, "get_reactions", return_value=(3, 1, [], [])), \
             patch.object(ev, "close_event_issue"), \
             patch.object(ev, "apply_event_effects"), \
             patch.object(ev, "fire_chained_event") as mock_chain:
            result = ev.check_event_expiry(0)
        assert result is True
        mock_chain.assert_called_once()
        active_after = json.loads((tmp_path / "world/active_event.json").read_text())
        assert active_after == {}
```

- [ ] **Step 5.5 — Run Group 7**

```
python -m pytest tests/test_event_generator.py::TestIntegration -v
```

Expected: 4 PASSED.

- [ ] **Step 5.6 — Run full test suite**

```
python -m pytest tests/ -q
```

Expected: all PASSED, 0 failures.

- [ ] **Step 5.7 — Syntax check**

```
python -c "import ast; ast.parse(open('scripts/tally_votes.py').read()); print('OK')"
python -c "import ast; ast.parse(open('scripts/engine/event_generator.py').read()); print('OK')"
python -c "import ast; ast.parse(open('scripts/engine/events.py').read()); print('OK')"
```

Expected: `OK` three times.

- [ ] **Step 5.8 — Reset world state (per CLAUDE.md)**

```
git checkout world/state.json
```

- [ ] **Step 5.9 — Commit**

```
git add scripts/engine/events.py scripts/engine/constants.py tests/test_events.py tests/test_event_generator.py
git commit -m "feat: wire LLM dynamic events into fire_random_event and fire_chained_event"
```

---

### Task 6: Push

- [ ] **Step 6.1 — Final full suite run**

```
python -m pytest tests/ -q
```

Expected: all PASSED.

- [ ] **Step 6.2 — Push**

```
git push origin master
```
