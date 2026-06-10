"""
Unit tests for Gitizens world engine logic.
Run with: pytest tests/test_engine.py -v
No GitHub API calls; only pure-Python functions are tested.
"""
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import helpers — patch file I/O so tests don't touch real world/ files
# ---------------------------------------------------------------------------

# Minimal state used as a base for most tests
BASE_STATE = {
    "era": "Founding Era",
    "laws_count": 8,
    "treasury": 200,
    "education": 60,
    "industry": 35,
    "welfare": 70,
    "green_policy": 70,
    "defense": 35,
    "population": 1000,
    "pollution": 0,
    "stability": 79,
}


def _import_module():
    """Import tally_votes with env vars stubbed out."""
    import importlib, os
    os.environ.setdefault("GITHUB_TOKEN", "test-token")
    os.environ.setdefault("GITHUB_REPOSITORY", "test/repo")
    # Stub out openai so import doesn't fail without real credentials
    sys.modules.setdefault("openai", MagicMock())
    sys.modules.setdefault("yaml", __import__("yaml"))
    import scripts.tally_votes as tv
    return tv


tv = _import_module()


# ===========================================================================
# determine_era
# ===========================================================================

class TestDetermineEra:
    def test_founding_era_default(self):
        state = {**BASE_STATE, "industry": 20, "education": 30, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Founding Era"

    def test_industrial_era(self):
        state = {**BASE_STATE, "industry": 65, "education": 55, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Industrial Era"

    def test_industrial_era_boundary_exact(self):
        state = {**BASE_STATE, "industry": 60, "education": 50, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Industrial Era"

    def test_industrial_era_just_below(self):
        state = {**BASE_STATE, "industry": 59, "education": 50, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Founding Era"

    def test_modern_era(self):
        state = {**BASE_STATE,
                 "education": 65, "industry": 65, "welfare": 65,
                 "green_policy": 65, "defense": 65, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Modern Era"

    def test_golden_age(self):
        state = {**BASE_STATE,
                 "education": 82, "industry": 81, "welfare": 83,
                 "green_policy": 80, "defense": 80, "pollution": 0, "stability": 82}
        assert tv.determine_era(state) == "Golden Age"

    def test_golden_age_needs_all_above_80(self):
        state = {**BASE_STATE,
                 "education": 82, "industry": 79, "welfare": 83,
                 "green_policy": 80, "defense": 80, "pollution": 0, "stability": 82}
        # industry = 79 → not Golden Age; all >= 65 → Modern Era
        assert tv.determine_era(state) == "Modern Era"

    def test_crisis_age_pollution(self):
        state = {**BASE_STATE, "pollution": 75, "stability": 79}
        assert tv.determine_era(state) == "Crisis Age"

    def test_crisis_age_stability(self):
        state = {**BASE_STATE, "pollution": 0, "stability": 25}
        assert tv.determine_era(state) == "Crisis Age"

    def test_crisis_overrides_golden(self):
        # Even if all policy metrics are high, pollution crisis wins
        state = {**BASE_STATE,
                 "education": 90, "industry": 90, "welfare": 90,
                 "green_policy": 90, "defense": 90,
                 "pollution": 80, "stability": 90}
        assert tv.determine_era(state) == "Crisis Age"


# ===========================================================================
# Idle economy (via world_autonomous_tick)
# ===========================================================================

class TestIdleEconomy:
    def _run_tick(self, state: dict) -> dict:
        """Run one tick against a temporary state file; return new state."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with patch.object(tv, "read_state", return_value=dict(state)), \
                 patch.object(tv, "write_state") as mock_write:
                tv.world_autonomous_tick()
                if mock_write.called:
                    return mock_write.call_args[0][0]
                return state

    def test_industrial_income(self):
        state = {**BASE_STATE, "industry": 30}  # 30//10 = 3 GC
        new = self._run_tick(state)
        assert new["treasury"] == BASE_STATE["treasury"] + 3 + (new["population"] // 500)

    def test_industrial_income_max(self):
        state = {**BASE_STATE, "industry": 80}  # 80//10 = 8 GC
        new = self._run_tick(state)
        pop_income = new["population"] // 500
        assert new["treasury"] == BASE_STATE["treasury"] + 8 + pop_income

    def test_population_income(self):
        state = {**BASE_STATE, "population": 1000}  # 1000//500 = 2 GC
        new = self._run_tick(state)
        ind_income = state["industry"] // 10
        assert new["treasury"] == state["treasury"] + ind_income + (new["population"] // 500)

    def test_welfare_population_bonus(self):
        base = {**BASE_STATE, "welfare": 70}  # >60 triggers +100 extra
        new = self._run_tick(base)
        # base pop delta: 50 (welfare>=40) + 100 (welfare>60) = 150
        assert new["population"] == base["population"] + 150

    def test_pollution_population_penalty(self):
        state = {**BASE_STATE, "welfare": 70, "industry": 80, "green_policy": 0}
        # ind - grn = 80 → pol_delta = +1 → new_pol = 1
        # pop_delta = 50+100=150, no pollution penalty since new_pol < 70
        new = self._run_tick(state)
        assert new["pollution"] == 1

    def test_high_pollution_population_penalty(self):
        state = {**BASE_STATE, "pollution": 70, "welfare": 70, "industry": 80, "green_policy": 0}
        new = self._run_tick(state)
        # new_pol = 71 >= 70 → pop_delta -= 50; so 150-50=100
        assert new["population"] == state["population"] + 100

    def test_era_recomputed_in_tick(self):
        state = {**BASE_STATE, "industry": 65, "education": 55, "pollution": 0, "stability": 79}
        new = self._run_tick(state)
        assert new["era"] == "Industrial Era"


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
        with patch.object(tv, "load_event_pool", return_value=self.SAMPLE_POOL):
            # 15% chance — force it by mocking random
            with patch("scripts.tally_votes.random.random", return_value=0.05):
                with patch("scripts.tally_votes.random.choices",
                           side_effect=lambda pop, weights, k: [pop[0]]) as mock_choices:
                    result = tv.fire_random_event(state)
                    chosen_pool = mock_choices.call_args[0][0]
                    # edu=30 < 70, so evt-test-edu and evt-test-asteroid must be excluded
                    assert all(e["id"] == "evt-test-common" for e in chosen_pool)

    def test_high_education_unlocks_events(self):
        state = {**BASE_STATE, "education": 75}
        with patch.object(tv, "load_event_pool", return_value=self.SAMPLE_POOL):
            with patch("scripts.tally_votes.random.random", return_value=0.05):
                with patch("scripts.tally_votes.random.choices",
                           side_effect=lambda pop, weights, k: [pop[0]]) as mock_choices:
                    tv.fire_random_event(state)
                    chosen_pool = mock_choices.call_args[0][0]
                    assert len(chosen_pool) == 3  # all three eligible

    def test_edu_bonus_increases_rare_weight(self):
        state = {**BASE_STATE, "education": 75}  # >70 → edu_bonus = 5
        with patch.object(tv, "load_event_pool", return_value=self.SAMPLE_POOL):
            with patch("scripts.tally_votes.random.random", return_value=0.05):
                with patch("scripts.tally_votes.random.choices",
                           side_effect=lambda pop, weights, k: [pop[0]]) as mock_choices:
                    tv.fire_random_event(state)
                    weights = mock_choices.call_args[1]["weights"]
                    # rare weight = 10 + 5 = 15, legendary = 5 + 5 = 10, common = 60
                    assert weights[1] == 15   # rare + edu_bonus
                    assert weights[2] == 10   # legendary + edu_bonus
                    assert weights[0] == 60   # common unchanged

    def test_no_trigger_at_15_percent_boundary(self):
        state = {**BASE_STATE}
        with patch.object(tv, "load_event_pool", return_value=self.SAMPLE_POOL):
            with patch("scripts.tally_votes.random.random", return_value=0.16):
                result = tv.fire_random_event(state)
                assert result is None

    def test_empty_pool_returns_none(self):
        state = {**BASE_STATE}
        with patch.object(tv, "load_event_pool", return_value=[]):
            with patch("scripts.tally_votes.random.random", return_value=0.05):
                result = tv.fire_random_event(state)
                assert result is None


# ===========================================================================
# apply_event_effects
# ===========================================================================

class TestApplyEventEffects:
    def _apply(self, state: dict, effects: dict, key: str = "immediate_effects") -> dict:
        event = {"immediate_effects": effects, "default_consequence": effects,
                 "response_consequence": effects}
        captured = {}
        with patch.object(tv, "read_state", return_value=dict(state)), \
             patch.object(tv, "write_state", side_effect=lambda s: captured.update(s)):
            tv.apply_event_effects(event, key)
        return captured

    def test_policy_metric_clamped_to_100(self):
        new = self._apply(BASE_STATE, {"education": 50})
        assert new["education"] == min(100, BASE_STATE["education"] + 50)

    def test_policy_metric_clamped_to_0(self):
        state = {**BASE_STATE, "welfare": 3}
        new = self._apply(state, {"welfare": -10})
        assert new["welfare"] == 0

    def test_treasury_no_lower_bound(self):
        # treasury uses max(0,...) so it can't go negative
        state = {**BASE_STATE, "treasury": 50}
        new = self._apply(state, {"treasury": -200})
        assert new["treasury"] == 0

    def test_population_delta(self):
        new = self._apply(BASE_STATE, {"population": 500})
        assert new["population"] == BASE_STATE["population"] + 500

    def test_stability_clamped(self):
        state = {**BASE_STATE, "stability": 95}
        new = self._apply(state, {"stability": 10})
        assert new["stability"] == 100


# ===========================================================================
# check_event_expiry
# ===========================================================================

class TestCheckEventExpiry:
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
        with patch.object(tv, "load_active_event", return_value=event), \
             patch.object(tv, "apply_event_effects") as mock_apply, \
             patch.object(tv, "save_active_event") as mock_save:
            result = tv.check_event_expiry(0)
            assert result is False
            mock_apply.assert_not_called()

    def test_event_expired_no_response(self):
        event = self._make_event(5)  # 5h ago, expired
        with patch.object(tv, "load_active_event", return_value=event), \
             patch.object(tv, "apply_event_effects") as mock_apply, \
             patch.object(tv, "close_event_issue"), \
             patch.object(tv, "save_active_event") as mock_save:
            result = tv.check_event_expiry(0)  # no laws passed
            assert result is True
            mock_apply.assert_called_once_with(event, "default_consequence")
            mock_save.assert_called_once_with({})

    def test_event_expired_with_response(self):
        event = self._make_event(5)
        with patch.object(tv, "load_active_event", return_value=event), \
             patch.object(tv, "apply_event_effects") as mock_apply, \
             patch.object(tv, "close_event_issue"), \
             patch.object(tv, "save_active_event"):
            result = tv.check_event_expiry(2)  # 2 laws passed → responded
            assert result is True
            mock_apply.assert_called_once_with(event, "response_consequence")

    def test_no_active_event_returns_false(self):
        with patch.object(tv, "load_active_event", return_value={}):
            result = tv.check_event_expiry(1)
            assert result is False


# ===========================================================================
# append_history_snapshot
# ===========================================================================

class TestAppendHistorySnapshot:
    def test_snapshot_fields(self):
        written = []
        with patch.object(tv, "load_active_event", return_value={}), \
             patch("pathlib.Path.read_text", return_value="[]"), \
             patch("pathlib.Path.write_text", side_effect=lambda text, **kw: written.append(json.loads(text))):
            tv.append_history_snapshot(BASE_STATE)
        assert len(written) == 1
        snap = written[0][0]
        assert snap["tick"] == 1
        assert snap["education"] == BASE_STATE["education"]
        assert snap["era"] == BASE_STATE["era"]
        assert snap["active_event"] is None

    def test_snapshot_capped_at_100(self):
        existing = [{"tick": i} for i in range(1, 101)]  # 100 entries
        written = []
        with patch.object(tv, "load_active_event", return_value={}), \
             patch("pathlib.Path.read_text", return_value=json.dumps(existing)), \
             patch("pathlib.Path.write_text", side_effect=lambda text, **kw: written.append(json.loads(text))):
            tv.append_history_snapshot(BASE_STATE)
        result = written[0]
        assert len(result) == 100
        assert result[-1]["tick"] == 101  # new snapshot appended, oldest dropped


# ===========================================================================
# get_reactions voter lists
# ===========================================================================

class TestGetReactions:
    def test_returns_voter_lists(self):
        mock_data = [
            {"user": {"login": "alice"}, "content": "+1"},
            {"user": {"login": "bob"},   "content": "-1"},
            {"user": {"login": "carol"}, "content": "+1"},
            {"user": {"login": "alice"}, "content": "-1"},  # alice changed her vote
        ]
        with patch.object(tv, "gh_json", return_value=mock_data):
            for_c, against_c, for_v, against_v = tv.get_reactions(42)
        # alice's last vote is -1
        assert for_c == 1
        assert against_c == 2
        assert for_v == ["carol"]
        assert sorted(against_v) == ["alice", "bob"]

    def test_empty_reactions(self):
        with patch.object(tv, "gh_json", return_value=[]):
            for_c, against_c, for_v, against_v = tv.get_reactions(99)
        assert for_c == 0 and against_c == 0
        assert for_v == [] and against_v == []
