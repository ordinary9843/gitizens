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
