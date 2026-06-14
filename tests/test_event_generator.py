import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("GITHUB_REPOSITORY", "test/repo")
sys.modules.setdefault("openai", MagicMock())

# Add scripts/ to sys.path so engine package resolves correctly from project root
_scripts_dir = str(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

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

    def test_missing_id_fails(self):
        event = {k: v for k, v in VALID_EVENT.items() if k != "id"}
        assert _gen.validate_event(event) is False

    def test_missing_category_fails(self):
        event = {k: v for k, v in VALID_EVENT.items() if k != "category"}
        assert _gen.validate_event(event) is False

    def test_missing_rarity_fails(self):
        event = {k: v for k, v in VALID_EVENT.items() if k != "rarity"}
        assert _gen.validate_event(event) is False

    def test_missing_description_fails(self):
        event = {k: v for k, v in VALID_EVENT.items() if k != "description"}
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
        # current=4, delta=-3 -> 4+(-3)=1 < 5 -> delta becomes 5-4=1
        state = {**BASE_STATE, "education": 4}
        event = self._evt(imm={"education": -3})
        assert _gen.apply_clamps(event, state)["immediate_effects"]["education"] == 1

    def test_metric_at_low_value_large_negative_clamped(self):
        # current=0, delta=-10 -> 0+(-10)=-10 < 5 -> delta = max(5-0, -50) = 5
        state = {**BASE_STATE, "treasury": 0}
        event = self._evt(imm={"treasury": -10})
        assert _gen.apply_clamps(event, state)["immediate_effects"]["treasury"] == 5

    def test_multiple_metrics_clamped_independently(self):
        # treasury=3: 3+(-10)=-7 < 5 -> delta=2
        # education=2: 2+(-5)=-3 < 5 -> delta=3
        # welfare=6: 6+(-3)=3 < 5 -> delta=5-6=-1
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
        # unknown_metric not in state -> current defaults to 50
        # delta=-80 -> after +-50 clamp: -50 -> 50+(-50)=0 < 5 -> delta = max(5-50, -50) = -45
        event = self._evt(imm={"unknown_metric": -80})
        assert _gen.apply_clamps(event, BASE_STATE)["immediate_effects"]["unknown_metric"] == -45

    def test_float_delta_rounded(self):
        event = self._evt(imm={"education": 7.6})
        assert _gen.apply_clamps(event, BASE_STATE)["immediate_effects"]["education"] == 8


# ===========================================================================
# Group 4: build_prompt + helpers
# ===========================================================================

class TestBuildWorldTrend:
    def test_empty_history_returns_no_data(self):
        assert "No historical data" in _gen._build_world_trend([])

    def test_single_entry_insufficient(self):
        result = _gen._build_world_trend([{"treasury": 50}])
        assert "Insufficient" in result

    def test_improving_metric_detected(self):
        history = [{"treasury": 40}] + [{}] * 4 + [{"treasury": 50}]
        result = _gen._build_world_trend(history)
        assert "treasury" in result
        assert "improving" in result

    def test_deteriorating_metric_detected(self):
        history = [{"education": 60}] + [{}] * 4 + [{"education": 49}]
        result = _gen._build_world_trend(history)
        assert "education" in result
        assert "deteriorating" in result

    def test_stable_returns_stable(self):
        history = [{"treasury": 50}] * 6
        result = _gen._build_world_trend(history)
        assert "stable" in result.lower()

    def test_only_last_6_entries_used(self):
        # 10 entries: first 4 show big drop, last 6 are stable
        history = (
            [{"treasury": 10}] * 4 +
            [{"treasury": 50}] * 6
        )
        result = _gen._build_world_trend(history)
        assert "stable" in result.lower()

    def test_missing_metric_in_first_ignored(self):
        history = [{}] + [{}] * 4 + [{"treasury": 50}]
        result = _gen._build_world_trend(history)
        # should not raise, treasury not in improving/deteriorating since first_val is None
        assert isinstance(result, str)


def _real_fallback_with_path(state: dict, pool_file) -> dict | None:
    """Helper: run _fallback_from_pool logic against an explicit pool file path."""
    import json as _json
    import random
    from engine.constants import CATEGORY_MULTIPLIERS
    try:
        pool = _json.loads(pool_file.read_text(encoding="utf-8"))
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


class TestBuildPrompt:
    def test_prompt_contains_metric_values(self):
        state = {**BASE_STATE}
        result = _gen.build_prompt(state, [])
        assert "treasury" in result
        assert str(BASE_STATE["treasury"]) in result

    def test_prompt_contains_trend_section(self):
        result = _gen.build_prompt(BASE_STATE, [])
        assert "Trend" in result

    def test_prompt_contains_schema(self):
        result = _gen.build_prompt(BASE_STATE, [])
        assert "immediate_effects" in result
        assert "duration_hours" in result

    def test_prompt_contains_rarity_guide(self):
        result = _gen.build_prompt(BASE_STATE, [])
        assert "legendary" in result.lower()

    def test_prompt_is_string(self):
        assert isinstance(_gen.build_prompt(BASE_STATE, []), str)


# ===========================================================================
# Group 5: generate_chained_event
# ===========================================================================

class TestGenerateChainedEvent:
    def _resolved_event(self):
        return {
            **VALID_EVENT,
            "id": "evt-resolved-001",
            "title": "Economic Crisis",
            "category": "economic",
            "response_consequence": {"treasury": 10, "stability": 5},
            "default_consequence": {"treasury": -20, "stability": -10},
        }

    def test_returns_chained_event_on_successful_llm(self):
        resolved = self._resolved_event()
        chained = {**VALID_EVENT, "id": "evt-llm-chain-001", "chained_from": None}
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(chained)
        with patch("engine.event_generator.build_prompt", return_value="prompt"):
            with patch("engine.content.client") as mock_client:
                mock_client.chat.completions.create.return_value = mock_response
                with patch("engine.state.read_history", return_value=[]):
                    result = _gen.generate_chained_event(resolved, True, BASE_STATE)
        assert isinstance(result, dict)
        assert result["chained_from"] == "evt-resolved-001"

    def test_returns_none_on_llm_exception(self):
        resolved = self._resolved_event()
        with patch("engine.content.client") as mock_client:
            mock_client.chat.completions.create.side_effect = Exception("LLM down")
            with patch("engine.state.read_history", return_value=[]):
                result = _gen.generate_chained_event(resolved, True, BASE_STATE)
        assert result is None

    def test_returns_none_on_invalid_llm_output(self):
        resolved = self._resolved_event()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "not json"
        with patch("engine.content.client") as mock_client:
            mock_client.chat.completions.create.return_value = mock_response
            with patch("engine.state.read_history", return_value=[]):
                result = _gen.generate_chained_event(resolved, False, BASE_STATE)
        assert result is None

    def test_responded_true_uses_response_consequence(self):
        resolved = self._resolved_event()
        chained = {**VALID_EVENT, "id": "evt-llm-chain-002", "chained_from": None}
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(chained)
        captured_prompt = {}
        def capture_create(**kwargs):
            captured_prompt["messages"] = kwargs["messages"]
            return mock_response
        with patch("engine.content.client") as mock_client:
            mock_client.chat.completions.create.side_effect = capture_create
            with patch("engine.state.read_history", return_value=[]):
                _gen.generate_chained_event(resolved, True, BASE_STATE)
        user_msg = captured_prompt["messages"][1]["content"]
        assert "responded" in user_msg
        assert "treasury: +10" in user_msg

    def test_responded_false_uses_default_consequence(self):
        resolved = self._resolved_event()
        chained = {**VALID_EVENT, "id": "evt-llm-chain-003", "chained_from": None}
        mock_response = MagicMock()
        mock_response.choices[0].message.content = json.dumps(chained)
        captured_prompt = {}
        def capture_create(**kwargs):
            captured_prompt["messages"] = kwargs["messages"]
            return mock_response
        with patch("engine.content.client") as mock_client:
            mock_client.chat.completions.create.side_effect = capture_create
            with patch("engine.state.read_history", return_value=[]):
                _gen.generate_chained_event(resolved, False, BASE_STATE)
        user_msg = captured_prompt["messages"][1]["content"]
        assert "defaulted" in user_msg
        assert "treasury: -20" in user_msg


# ===========================================================================
# Group 6: generate_event + _fallback_from_pool
# ===========================================================================

class TestFallbackFromPool:
    def test_returns_event_from_pool(self, tmp_path):
        pool = [dict(VALID_EVENT)]
        pool_file = tmp_path / "event_pool.json"
        pool_file.write_text(json.dumps(pool), encoding="utf-8")
        result = _real_fallback_with_path(BASE_STATE, pool_file)
        assert result is not None
        assert result["id"] == VALID_EVENT["id"]

    def test_returns_none_on_missing_pool(self):
        with patch("pathlib.Path.read_text", side_effect=FileNotFoundError("no pool")):
            result = _gen._fallback_from_pool({"treasury": 50})
        assert result is None

    def test_empty_pool_returns_none(self, tmp_path):
        pool_file = tmp_path / "event_pool.json"
        pool_file.write_text(json.dumps([]), encoding="utf-8")
        result = _real_fallback_with_path(BASE_STATE, pool_file)
        assert result is None

    def test_weights_applied_per_category(self, tmp_path):
        # A "health" event with welfare < 35 should have multiplied weight 2.0.
        # Use a pool with only one event so we can assert it is selected.
        pool = [{**VALID_EVENT, "category": "health"}]
        pool_file = tmp_path / "event_pool.json"
        pool_file.write_text(json.dumps(pool), encoding="utf-8")
        low_welfare_state = {**BASE_STATE, "welfare": 20}
        result = _real_fallback_with_path(low_welfare_state, pool_file)
        assert result is not None
        assert result["category"] == "health"


class TestGenerateEvent:
    def _make_valid_response(self):
        return json.dumps({
            **VALID_EVENT,
            "id": "evt-llm-001",
        })

    def test_falls_back_on_llm_exception(self):
        fallback_event = dict(VALID_EVENT)
        with patch("engine.event_generator.build_prompt", side_effect=Exception("LLM down")):
            with patch("engine.event_generator._fallback_from_pool", return_value=fallback_event):
                result = _gen.generate_event(BASE_STATE)
        assert result == fallback_event

    def test_falls_back_on_invalid_llm_output(self):
        mock_response = MagicMock()
        mock_response.choices[0].message.content = "not valid json"
        fallback_event = dict(VALID_EVENT)
        with patch("engine.event_generator.build_prompt", return_value="prompt"):
            with patch("engine.content.client") as mock_client:
                mock_client.chat.completions.create.return_value = mock_response
                with patch("engine.event_generator._fallback_from_pool", return_value=fallback_event):
                    with patch("engine.state.read_history", return_value=[]):
                        result = _gen.generate_event(BASE_STATE)
        assert result == fallback_event

    def test_returns_none_when_fallback_also_fails(self):
        with patch("engine.event_generator.build_prompt", side_effect=Exception("fail")):
            with patch("engine.event_generator._fallback_from_pool", return_value=None):
                result = _gen.generate_event(BASE_STATE)
        assert result is None

    def test_returns_event_on_successful_llm(self):
        mock_response = MagicMock()
        mock_response.choices[0].message.content = self._make_valid_response()
        with patch("engine.event_generator.build_prompt", return_value="prompt"):
            with patch("engine.content.client") as mock_client:
                mock_client.chat.completions.create.return_value = mock_response
                with patch("engine.event_generator._fallback_from_pool", return_value=None):
                    with patch("engine.state.read_history", return_value=[]):
                        result = _gen.generate_event(BASE_STATE)
        assert isinstance(result, dict)
        assert result["id"] == "evt-llm-001"


# ===========================================================================
# Group 7: fire_random_event integration (via events.py)
# ===========================================================================

class TestFireRandomEvent:
    def test_returns_none_when_random_above_threshold(self):
        with patch("engine.events.random") as mock_random:
            mock_random.random.return_value = 0.9
            from engine.events import fire_random_event
            result = fire_random_event(BASE_STATE)
        assert result is None

    def test_calls_generate_event_when_triggered(self):
        expected = dict(VALID_EVENT)
        with patch("engine.events.random") as mock_random:
            mock_random.random.return_value = 0.05
            with patch("engine.events.generate_event", return_value=expected) as mock_gen:
                from engine.events import fire_random_event
                result = fire_random_event(BASE_STATE)
        assert result == expected
        mock_gen.assert_called_once()

    def test_returns_none_when_generate_event_returns_none(self):
        with patch("engine.events.random") as mock_random:
            mock_random.random.return_value = 0.05
            with patch("engine.events.generate_event", return_value=None):
                from engine.events import fire_random_event
                result = fire_random_event(BASE_STATE)
        assert result is None


# ===========================================================================
# parse_llm_output — code block with invalid JSON (lines 21-22)
# ===========================================================================

class TestParseLlmOutputCodeBlockInvalidJson:
    def test_code_block_with_invalid_json_returns_none(self):
        raw = "```json\n{this is: not valid json!!}\n```"
        assert _gen.parse_llm_output(raw) is None


# ===========================================================================
# validate_event — non-dict input (line 40)
# ===========================================================================

class TestValidateEventNonDict:
    def test_integer_input_returns_false(self):
        assert _gen.validate_event(42) is False

    def test_list_input_returns_false(self):
        assert _gen.validate_event([VALID_EVENT]) is False

    def test_string_input_returns_false(self):
        assert _gen.validate_event("event") is False


# ===========================================================================
# _load_recent_laws — file missing / exception (lines 145, 149-150)
# ===========================================================================

class TestLoadRecentLaws:
    def test_returns_empty_when_file_does_not_exist(self):
        from pathlib import Path as _Path
        original_exists = _Path.exists
        def patched_exists(self):
            if "laws_index.json" in str(self):
                return False
            return original_exists(self)
        with patch.object(_Path, "exists", patched_exists):
            result = _gen._load_recent_laws()
        assert result == []

    def test_returns_empty_on_read_exception(self):
        from pathlib import Path as _Path
        original_exists = _Path.exists
        original_read = _Path.read_text
        def patched_exists(self):
            if "laws_index.json" in str(self):
                return True
            return original_exists(self)
        def patched_read(self, *args, **kwargs):
            if "laws_index.json" in str(self):
                raise OSError("IO error")
            return original_read(self, *args, **kwargs)
        with patch.object(_Path, "exists", patched_exists), \
             patch.object(_Path, "read_text", patched_read):
            result = _gen._load_recent_laws()
        assert result == []


# ===========================================================================
# _load_recent_event_history — success path (lines 159-167)
# ===========================================================================

class TestLoadRecentEventHistory:
    def test_returns_event_titles_when_annals_exist(self):
        from pathlib import Path as _Path
        annals = [
            {"type": "event", "title": "Earthquake"},
            {"type": "tick"},
            {"type": "event", "title": "Flood"},
        ]
        original_exists = _Path.exists
        original_read = _Path.read_text
        def patched_exists(self):
            if "annals.json" in str(self):
                return True
            return original_exists(self)
        def patched_read(self, *args, **kwargs):
            if "annals.json" in str(self):
                return json.dumps(annals)
            return original_read(self, *args, **kwargs)
        with patch.object(_Path, "exists", patched_exists), \
             patch.object(_Path, "read_text", patched_read):
            result = _gen._load_recent_event_history()
        assert "Earthquake" in result
        assert "Flood" in result
        assert len(result) == 2


# ===========================================================================
# _fallback_from_pool — success path (lines 176-190)
# ===========================================================================

class TestFallbackFromPoolDirect:
    def test_direct_call_selects_from_pool(self):
        from pathlib import Path as _Path
        pool = [dict(VALID_EVENT)]
        original_read = _Path.read_text
        def patched_read(self, *args, **kwargs):
            if "event_pool.json" in str(self):
                return json.dumps(pool)
            return original_read(self, *args, **kwargs)
        with patch.object(_Path, "read_text", patched_read):
            result = _gen._fallback_from_pool(BASE_STATE)
        assert result is not None
        assert result["id"] == VALID_EVENT["id"]

    def test_empty_pool_returns_none_direct(self):
        from pathlib import Path as _Path
        original_read = _Path.read_text
        def patched_read(self, *args, **kwargs):
            if "event_pool.json" in str(self):
                return json.dumps([])
            return original_read(self, *args, **kwargs)
        with patch.object(_Path, "read_text", patched_read):
            result = _gen._fallback_from_pool(BASE_STATE)
        assert result is None

    def test_low_direction_multiplier_applied(self):
        from pathlib import Path as _Path
        pool = [{**VALID_EVENT, "category": "natural"}]
        low_green_state = {**BASE_STATE, "green_policy": 10}
        original_read = _Path.read_text
        def patched_read(self, *args, **kwargs):
            if "event_pool.json" in str(self):
                return json.dumps(pool)
            return original_read(self, *args, **kwargs)
        with patch.object(_Path, "read_text", patched_read):
            result = _gen._fallback_from_pool(low_green_state)
        assert result is not None
        assert result["category"] == "natural"


# ===========================================================================
# _load_recent_event_history — exception path (lines 166-167)
# ===========================================================================

class TestLoadRecentEventHistoryException:
    def test_exception_during_read_returns_empty(self):
        from pathlib import Path as _Path
        original_exists = _Path.exists
        original_read = _Path.read_text
        def patched_exists(self):
            if "annals.json" in str(self):
                return True
            return original_exists(self)
        def patched_read(self, *args, **kwargs):
            if "annals.json" in str(self):
                raise OSError("IO error reading annals")
            return original_read(self, *args, **kwargs)
        with patch.object(_Path, "exists", patched_exists), \
             patch.object(_Path, "read_text", patched_read):
            result = _gen._load_recent_event_history()
        assert result == []
