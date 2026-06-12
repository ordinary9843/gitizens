import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tests.helpers import (
    BASE_STATE, tv,
    _engine_gh, _engine_world, _engine_events,
    _engine_chronicle, _engine_content, _engine_proposals,
    _make_category,
)


# ===========================================================================
# Event pool loading and eligibility
# ===========================================================================

class TestEventEligibility:
    SAMPLE_POOL = [
        {"id": "evt-test-common", "rarity": "common", "trigger_conditions": {},
         "immediate_effects": {}, "default_consequence": {}, "response_consequence": {}},
        {"id": "evt-test-edu", "rarity": "rare", "trigger_conditions": {"education": {"min": 70}},
         "immediate_effects": {}, "default_consequence": {}, "response_consequence": {}},
        {"id": "evt-test-asteroid", "rarity": "legendary",
         "trigger_conditions": {"education": {"min": 70}},
         "immediate_effects": {}, "default_consequence": {}, "response_consequence": {}},
    ]

    def test_eligible_without_conditions(self):
        state = {**BASE_STATE, "education": 30}
        with patch.object(_engine_events, "load_event_pool", return_value=self.SAMPLE_POOL):
            # 15% chance — force it by mocking random
            with patch("engine.events.random.random", return_value=0.05):
                with patch("engine.events.random.choices",
                           side_effect=lambda pop, weights, k: [pop[0]]) as mock_choices:
                    result = tv.fire_random_event(state)
                    chosen_pool = mock_choices.call_args[0][0]
                    # edu=30 < 70, so evt-test-edu and evt-test-asteroid must be excluded
                    assert all(e["id"] == "evt-test-common" for e in chosen_pool)

    def test_high_education_unlocks_events(self):
        state = {**BASE_STATE, "education": 75}
        with patch.object(_engine_events, "load_event_pool", return_value=self.SAMPLE_POOL):
            with patch("engine.events.random.random", return_value=0.05):
                with patch("engine.events.random.choices",
                           side_effect=lambda pop, weights, k: [pop[0]]) as mock_choices:
                    tv.fire_random_event(state)
                    chosen_pool = mock_choices.call_args[0][0]
                    assert len(chosen_pool) == 3  # all three eligible

    def test_edu_bonus_increases_rare_weight(self):
        state = {**BASE_STATE, "education": 75}  # >70 → edu_bonus = 5
        with patch.object(_engine_events, "load_event_pool", return_value=self.SAMPLE_POOL):
            with patch("engine.events.random.random", return_value=0.05):
                with patch("engine.events.random.choices",
                           side_effect=lambda pop, weights, k: [pop[0]]) as mock_choices:
                    tv.fire_random_event(state)
                    weights = mock_choices.call_args[1]["weights"]
                    # rare weight = 10 + 5 = 15, legendary = 5 + 5 = 10, common = 60
                    assert weights[1] == 15   # rare + edu_bonus
                    assert weights[2] == 10   # legendary + edu_bonus
                    assert weights[0] == 60   # common unchanged

    def test_no_trigger_at_15_percent_boundary(self):
        state = {**BASE_STATE}
        with patch.object(_engine_events, "load_event_pool", return_value=self.SAMPLE_POOL):
            with patch("engine.events.random.random", return_value=0.16):
                result = tv.fire_random_event(state)
                assert result is None

    def test_empty_pool_returns_none(self):
        state = {**BASE_STATE}
        with patch.object(_engine_events, "load_event_pool", return_value=[]):
            with patch("engine.events.random.random", return_value=0.05):
                result = tv.fire_random_event(state)
                assert result is None


# ===========================================================================
# check_event_expiry — mocked version (first definition, previously dead code)
# ===========================================================================

class TestCheckEventExpiryMocked:
    BASE_EVENT = {
        "id": "evt-test",
        "title": "Test Event",
        "fired_at": None,
        "duration_hours": 4,
        "issue_number": 0,
        "default_consequence": {},
        "response_consequence": {"treasury": 50},
    }

    def _make_event(self, hours_ago: float) -> dict:
        fired = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        return {**self.BASE_EVENT, "fired_at": fired}

    def test_event_not_expired_yet(self):
        event = self._make_event(3)  # 3h ago, duration=4h
        with patch.object(_engine_events, "load_active_event", return_value=event), \
             patch.object(_engine_events, "apply_event_effects") as mock_apply, \
             patch.object(_engine_events, "save_active_event") as mock_save:
            result = tv.check_event_expiry(0)
            assert result is False
            mock_apply.assert_not_called()

    def test_event_expired_no_response(self):
        event = self._make_event(5)  # 5h ago, expired
        with patch.object(_engine_events, "load_active_event", return_value=event), \
             patch.object(_engine_events, "apply_event_effects") as mock_apply, \
             patch.object(_engine_events, "close_event_issue"), \
             patch.object(_engine_events, "save_active_event") as mock_save, \
             patch.object(_engine_events, "fire_chained_event"):
            result = tv.check_event_expiry(0)  # no laws passed
            assert result is True
            mock_apply.assert_called_once_with(event, "default_consequence")
            mock_save.assert_called_once_with({})

    def test_event_expired_with_response(self):
        event = self._make_event(5)
        with patch.object(_engine_events, "load_active_event", return_value=event), \
             patch.object(_engine_events, "apply_event_effects") as mock_apply, \
             patch.object(_engine_events, "close_event_issue"), \
             patch.object(_engine_events, "save_active_event"), \
             patch.object(_engine_events, "fire_chained_event"):
            result = tv.check_event_expiry(2)  # 2 laws passed → responded
            assert result is True
            mock_apply.assert_called_once_with(event, "response_consequence")

    def test_no_active_event_returns_false(self):
        with patch.object(_engine_events, "load_active_event", return_value={}):
            result = tv.check_event_expiry(1)
            assert result is False


# ===========================================================================
# apply_crisis_multiplier
# ===========================================================================

class TestCrisisMultiplier:
    def test_no_crisis_returns_unchanged(self):
        effect = {"type": "policy", "changes": {"education": 10}}
        result = tv.apply_crisis_multiplier(effect, {"is_crisis": False})
        assert result["changes"]["education"] == 10

    def test_crisis_multiplies_changes(self):
        effect = {"type": "policy", "changes": {"welfare": 8, "education": 5}}
        active = {"is_crisis": True, "crisis_multiplier": 1.5}
        result = tv.apply_crisis_multiplier(effect, active)
        assert result["changes"]["welfare"] == 12   # 8 × 1.5
        assert result["changes"]["education"] == 8  # round(5 × 1.5) = 8

    def test_non_policy_type_unchanged(self):
        effect = {"type": "declaration"}
        active = {"is_crisis": True, "crisis_multiplier": 2.0}
        result = tv.apply_crisis_multiplier(effect, active)
        assert result == effect

    def test_empty_active_event_no_crash(self):
        effect = {"type": "policy", "changes": {"industry": 6}}
        result = tv.apply_crisis_multiplier(effect, {})
        assert result["changes"]["industry"] == 6

    def test_none_effect_data_returns_none(self):
        result = tv.apply_crisis_multiplier(None, {"is_crisis": True})
        assert result is None

    def test_original_dict_not_mutated(self):
        effect = {"type": "policy", "changes": {"defense": 10}}
        active = {"is_crisis": True, "crisis_multiplier": 2.0}
        result = tv.apply_crisis_multiplier(effect, active)
        assert effect["changes"]["defense"] == 10  # original unchanged
        assert result["changes"]["defense"] == 20


# ===========================================================================
# fire_chained_event
# ===========================================================================

class TestEventChain:
    BASE_CHAIN_EVENT = {
        "id": "evt-recovery",
        "title": "Recovery",
        "immediate_effects": {},
        "trigger_conditions": {},
        "rarity": "common",
        "duration_hours": 4,
        "default_consequence": {},
        "response_consequence": {},
    }

    def test_no_chain_field_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/active_event.json").write_text("{}")
        (tmp_path / "world/event_pool.json").write_text("[]")
        tv.fire_chained_event({"id": "evt-drought", "title": "Drought"}, responded=True)
        assert json.loads((tmp_path / "world/active_event.json").read_text()) == {}

    def test_chain_on_response_fires_next(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/active_event.json").write_text("{}")
        (tmp_path / "world/event_pool.json").write_text(json.dumps([self.BASE_CHAIN_EVENT]))
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        event = {"id": "evt-drought", "title": "Drought",
                 "triggers_next_on_response": "evt-recovery"}
        with patch.object(_engine_events, "open_event_issue", return_value=99), \
             patch.object(_engine_events, "apply_event_effects"):
            tv.fire_chained_event(event, responded=True)
        active = json.loads((tmp_path / "world/active_event.json").read_text())
        assert active.get("id") == "evt-recovery"
        assert active.get("chained_from") == "evt-drought"
        assert active.get("issue_number") == 99

    def test_chain_on_default_fires_different(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/active_event.json").write_text("{}")
        famine = {**self.BASE_CHAIN_EVENT, "id": "evt-famine", "title": "Famine"}
        (tmp_path / "world/event_pool.json").write_text(json.dumps([famine]))
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        event = {"id": "evt-drought", "title": "Drought",
                 "triggers_next_on_default": "evt-famine"}
        with patch.object(_engine_events, "open_event_issue", return_value=42), \
             patch.object(_engine_events, "apply_event_effects"):
            tv.fire_chained_event(event, responded=False)
        active = json.loads((tmp_path / "world/active_event.json").read_text())
        assert active.get("id") == "evt-famine"

    def test_no_chain_when_event_already_active(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        existing = {**self.BASE_CHAIN_EVENT, "id": "evt-existing", "fired_at": "2026-01-01T00:00:00+00:00"}
        (tmp_path / "world/active_event.json").write_text(json.dumps(existing))
        (tmp_path / "world/event_pool.json").write_text(json.dumps([self.BASE_CHAIN_EVENT]))
        event = {"id": "evt-drought", "title": "Drought",
                 "triggers_next_on_response": "evt-recovery"}
        with patch.object(tv, "open_event_issue") as mock_open:
            tv.fire_chained_event(event, responded=True)
            mock_open.assert_not_called()


# ===========================================================================
# check_event_expiry — timezone and duration_hours type safety
# ===========================================================================

class TestCheckEventExpiry:
    def _write_event(self, path, fired_ago_hours, duration_hours=4, extra=None):
        fired_at = (datetime.now(timezone.utc) - timedelta(hours=fired_ago_hours)).isoformat()
        evt = {"id": "evt-test", "title": "Test Event",
               "fired_at": fired_at, "duration_hours": duration_hours,
               "issue_number": 0, "immediate_effects": {},
               "default_consequence": {}, "response_consequence": {}}
        if extra:
            evt.update(extra)
        (path / "world/active_event.json").write_text(json.dumps(evt))

    def test_not_expired_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        self._write_event(tmp_path, fired_ago_hours=1, duration_hours=4)
        assert tv.check_event_expiry(0) is False

    def test_expired_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        (tmp_path / "world/active_event.json").write_text("{}")
        self._write_event(tmp_path, fired_ago_hours=5, duration_hours=4)
        def fake_run(cmd): return ""
        monkeypatch.setattr(tv, "run", fake_run)
        result = tv.check_event_expiry(0)
        assert result is True

    def test_malformed_fired_at_clears_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        (tmp_path / "world/active_event.json").write_text(
            json.dumps({"id": "x", "title": "Bad", "fired_at": "not-a-date",
                        "duration_hours": 4, "issue_number": 0}))
        result = tv.check_event_expiry(0)
        assert result is False
        active = json.loads((tmp_path / "world/active_event.json").read_text())
        assert active == {}

    def test_non_numeric_duration_defaults_to_4h(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        (tmp_path / "world/active_event.json").write_text("{}")
        fired_at = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        evt = {"id": "x", "title": "T", "fired_at": fired_at,
               "duration_hours": "bad_value", "issue_number": 0,
               "immediate_effects": {}, "default_consequence": {},
               "response_consequence": {}}
        (tmp_path / "world/active_event.json").write_text(json.dumps(evt))
        def fake_run(cmd): return ""
        monkeypatch.setattr(tv, "run", fake_run)
        # Should use 4h default → 5h ago > 4h → expired
        result = tv.check_event_expiry(0)
        assert result is True


# ===========================================================================
# check_event_expiry — reaction-based voting (Feature A)
# ===========================================================================

class TestCheckEventExpiryVoting:
    def _make_expired_event(self, issue_number: int = 42) -> dict:
        fired = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        return {
            "id": "evt-test", "title": "Test Event",
            "fired_at": fired, "duration_hours": 4,
            "issue_number": issue_number,
            "default_consequence": {"stability": -5},
            "response_consequence": {"treasury": 20},
        }

    def test_responded_when_for_votes_win(self):
        evt = self._make_expired_event(issue_number=42)
        with patch.object(_engine_events, "load_active_event", return_value=evt), \
             patch.object(_engine_events, "get_reactions", return_value=(3, 1, ["a","b","c"], ["d"])), \
             patch.object(_engine_events, "apply_event_effects") as mock_apply, \
             patch.object(_engine_events, "close_event_issue"), \
             patch.object(_engine_events, "save_active_event"), \
             patch.object(_engine_events, "fire_chained_event"):
            tv.check_event_expiry(0)
            mock_apply.assert_called_once_with(evt, "response_consequence")

    def test_default_when_against_votes_win(self):
        evt = self._make_expired_event(issue_number=42)
        with patch.object(_engine_events, "load_active_event", return_value=evt), \
             patch.object(_engine_events, "get_reactions", return_value=(1, 3, ["a"], ["b","c","d"])), \
             patch.object(_engine_events, "apply_event_effects") as mock_apply, \
             patch.object(_engine_events, "close_event_issue"), \
             patch.object(_engine_events, "save_active_event"), \
             patch.object(_engine_events, "fire_chained_event"):
            tv.check_event_expiry(0)
            mock_apply.assert_called_once_with(evt, "default_consequence")

    def test_default_when_no_votes(self):
        evt = self._make_expired_event(issue_number=42)
        with patch.object(_engine_events, "load_active_event", return_value=evt), \
             patch.object(_engine_events, "get_reactions", return_value=(0, 0, [], [])), \
             patch.object(_engine_events, "apply_event_effects") as mock_apply, \
             patch.object(_engine_events, "close_event_issue"), \
             patch.object(_engine_events, "save_active_event"), \
             patch.object(_engine_events, "fire_chained_event"):
            tv.check_event_expiry(0)
            mock_apply.assert_called_once_with(evt, "default_consequence")

    def test_fallback_to_laws_when_no_issue_number(self):
        evt = self._make_expired_event(issue_number=0)
        with patch.object(_engine_events, "load_active_event", return_value=evt), \
             patch.object(_engine_events, "apply_event_effects") as mock_apply, \
             patch.object(_engine_events, "close_event_issue"), \
             patch.object(_engine_events, "save_active_event"), \
             patch.object(_engine_events, "fire_chained_event"):
            tv.check_event_expiry(2)
            mock_apply.assert_called_once_with(evt, "response_consequence")

    def test_fallback_default_when_no_issue_no_laws(self):
        evt = self._make_expired_event(issue_number=0)
        with patch.object(_engine_events, "load_active_event", return_value=evt), \
             patch.object(_engine_events, "apply_event_effects") as mock_apply, \
             patch.object(_engine_events, "close_event_issue"), \
             patch.object(_engine_events, "save_active_event"), \
             patch.object(_engine_events, "fire_chained_event"):
            tv.check_event_expiry(0)
            mock_apply.assert_called_once_with(evt, "default_consequence")


# ===========================================================================
# CATEGORY_MULTIPLIERS — event weights respond to world state
# ===========================================================================

class TestCategoryMultipliers:
    _NATURAL_EVENT = {
        "id": "evt-nat", "category": "natural", "rarity": "common",
        "trigger_conditions": {},
        "immediate_effects": {}, "default_consequence": {}, "response_consequence": {},
    }
    _ECONOMIC_EVENT = {
        "id": "evt-eco", "category": "economic", "rarity": "common",
        "trigger_conditions": {},
        "immediate_effects": {}, "default_consequence": {}, "response_consequence": {},
    }
    _HEALTH_EVENT = {
        "id": "evt-hlt", "category": "health", "rarity": "common",
        "trigger_conditions": {},
        "immediate_effects": {}, "default_consequence": {}, "response_consequence": {},
    }
    _WEIRD_EVENT = {
        "id": "evt-weird", "category": "weird", "rarity": "common",
        "trigger_conditions": {},
        "immediate_effects": {}, "default_consequence": {}, "response_consequence": {},
    }

    def _get_weights(self, pool, state):
        captured = {}
        def _capture(eligible, weights, k):
            captured["weights"] = list(weights)
            return [eligible[0]]
        with patch.object(_engine_events, "load_event_pool", return_value=pool), \
             patch("engine.events.random.random", return_value=0.05), \
             patch("engine.events.random.choices", side_effect=_capture):
            tv.fire_random_event(state)
        return captured.get("weights", [])

    def test_low_green_boosts_natural_weight(self):
        state = {**BASE_STATE, "green_policy": 20}
        weights = self._get_weights([self._NATURAL_EVENT, self._ECONOMIC_EVENT], state)
        # natural: base 60 * 2.0 (green_policy=20 < 40)
        # economic: base 60, no green_policy multiplier
        assert weights[0] == 120.0
        assert weights[1] == 60

    def test_high_industry_boosts_economic_weight(self):
        # green_policy=50: above "low" threshold (40) and below "high" threshold (70)
        # so natural event gets no multiplier and stays at base 60
        state = {**BASE_STATE, "industry": 80, "treasury": 100, "green_policy": 50}
        weights = self._get_weights([self._NATURAL_EVENT, self._ECONOMIC_EVENT], state)
        # economic: base 60 * 1.5 (industry=80 >= 60)
        # natural: unaffected by industry, green_policy=50 is neutral
        assert weights[1] == 90.0
        assert weights[0] == 60

    def test_multipliers_stack_correctly(self):
        # economic with both industry>=60 AND treasury<50: mult = 1.5 * 1.4 = 2.1
        state = {**BASE_STATE, "industry": 70, "treasury": 20}
        weights = self._get_weights([self._ECONOMIC_EVENT], state)
        assert abs(weights[0] - 60 * 1.5 * 1.4) < 0.01

    def test_unaffected_category_unchanged(self):
        # "weird" is not in CATEGORY_MULTIPLIERS — weight stays at base 60
        state = {**BASE_STATE, "green_policy": 5, "welfare": 5, "defense": 5}
        weights = self._get_weights([self._WEIRD_EVENT], state)
        assert weights[0] == 60
