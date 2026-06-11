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


# ===========================================================================
# format_signatories
# ===========================================================================

class TestFormatSignatories:
    def test_small_list_shows_inline(self):
        result = tv.format_signatories(["a", "b", "c", "d", "e"], [])
        assert "<details>" not in result
        assert "@a" in result

    def test_large_list_uses_collapsible(self):
        voters = [f"user{i}" for i in range(15)]
        result = tv.format_signatories(voters, [])
        assert "<details>" in result
        assert "15 signatories" in result

    def test_empty_against_shows_dash(self):
        result = tv.format_signatories(["alice"], [])
        assert "—" in result

    def test_threshold_boundary_10_inline(self):
        voters = [f"u{i}" for i in range(10)]
        result = tv.format_signatories(voters, [])
        assert "<details>" not in result

    def test_threshold_boundary_11_collapsible(self):
        voters = [f"u{i}" for i in range(11)]
        result = tv.format_signatories(voters, [])
        assert "<details>" in result

    def test_mixed_voters_counted_together(self):
        for_v = [f"f{i}" for i in range(6)]
        against_v = [f"a{i}" for i in range(6)]
        result = tv.format_signatories(for_v, against_v)
        assert "<details>" in result
        assert "12 signatories" in result


# ===========================================================================
# track_citizen_activity / track_citizen_proposal
# ===========================================================================

class TestCitizenTracking:
    def test_new_citizen_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        tv.track_citizen_activity(["alice"], [])
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "alice" in data
        assert data["alice"]["total_votes"] == 1

    def test_existing_citizen_incremented(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        tv.track_citizen_activity(["alice"], [])
        tv.track_citizen_activity(["alice"], [])
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert data["alice"]["total_votes"] == 2

    def test_multiple_voters_all_recorded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        tv.track_citizen_activity(["alice", "bob"], ["charlie"])
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert len(data) == 3

    def test_track_proposal_increments_proposals(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        tv.track_citizen_proposal("alice")
        tv.track_citizen_proposal("alice")
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert data["alice"]["total_proposals"] == 2
        assert data["alice"]["total_votes"] == 0


# ===========================================================================
# check_proposal_cooldown / update_proposal_cooldown
# ===========================================================================

class TestProposalCooldown:
    def test_no_cooldown_file_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        effect = {"type": "policy", "changes": {"education": 10}}
        ok, _ = tv.check_proposal_cooldown(effect)
        assert ok

    def test_cooldown_active_blocks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": today}))
        effect = {"type": "policy", "changes": {"education": 10}}
        ok, reason = tv.check_proposal_cooldown(effect)
        assert not ok
        assert "education" in reason

    def test_cooldown_expired_allows(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        old_date = (datetime.now(timezone.utc) - timedelta(days=15)).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": old_date}))
        effect = {"type": "policy", "changes": {"education": 10}}
        ok, _ = tv.check_proposal_cooldown(effect)
        assert ok

    def test_non_policy_always_ok(self):
        ok, _ = tv.check_proposal_cooldown({"type": "declaration"})
        assert ok

    def test_none_effect_data_always_ok(self):
        ok, _ = tv.check_proposal_cooldown(None)
        assert ok

    def test_update_writes_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        effect = {"type": "policy", "changes": {"welfare": 5, "education": 3}}
        tv.update_proposal_cooldown(effect, "2026-06-11")
        data = json.loads((tmp_path / "world/proposal_cooldowns.json").read_text())
        assert data["welfare"] == "2026-06-11"
        assert data["education"] == "2026-06-11"

    def test_update_non_policy_skips(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        tv.update_proposal_cooldown({"type": "declaration"}, "2026-06-11")
        assert not (tmp_path / "world/proposal_cooldowns.json").exists()


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
        with patch.object(tv, "open_event_issue", return_value=99), \
             patch.object(tv, "apply_event_effects"):
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
        with patch.object(tv, "open_event_issue", return_value=42), \
             patch.object(tv, "apply_event_effects"):
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
# select_weekly_representatives
# ===========================================================================

class TestSelectRepresentatives:
    def test_top3_selected_by_votes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "alice": {"total_votes": 50, "total_proposals": 2, "last_active": "2026-06-11T00:00:00Z"},
            "bob":   {"total_votes": 30, "total_proposals": 1, "last_active": "2026-06-11T00:00:00Z"},
            "carol": {"total_votes": 20, "total_proposals": 0, "last_active": "2026-06-11T00:00:00Z"},
            "dave":  {"total_votes": 5,  "total_proposals": 0, "last_active": "2026-06-10T00:00:00Z"},
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        (tmp_path / "world/representatives.json").write_text(json.dumps({"selected_at": None}))
        with patch.object(tv, "get_or_create_dispatch_issue", return_value=13), \
             patch.object(tv, "run", return_value=""):
            tv.select_weekly_representatives()
        reps = json.loads((tmp_path / "world/representatives.json").read_text())
        assert reps["representatives"] == ["alice", "bob", "carol"]

    def test_no_reselection_before_7_days(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        (tmp_path / "world/representatives.json").write_text(
            json.dumps({"selected_at": yesterday, "representatives": ["alice"]}))
        tv.select_weekly_representatives()
        reps = json.loads((tmp_path / "world/representatives.json").read_text())
        assert reps["representatives"] == ["alice"]

    def test_fewer_than_3_citizens(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {"alice": {"total_votes": 5, "total_proposals": 0, "last_active": ""}}
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        (tmp_path / "world/representatives.json").write_text(json.dumps({"selected_at": None}))
        with patch.object(tv, "get_or_create_dispatch_issue", return_value=13), \
             patch.object(tv, "run", return_value=""):
            tv.select_weekly_representatives()
        reps = json.loads((tmp_path / "world/representatives.json").read_text())
        assert len(reps["representatives"]) == 1


# ===========================================================================
# generate_annals
# ===========================================================================

class TestAnnalsGeneration:
    def test_no_generation_before_interval(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/annals").mkdir(parents=True)
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        history = [{"tick": i + 1} for i in range(9)]  # ticks 1-9, last is 9 → 9%10≠0
        tv.generate_annals(history)
        assert not list((tmp_path / "world/annals").glob("*.md"))

    def test_no_generation_on_empty_history(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/annals").mkdir(parents=True)
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        tv.generate_annals([])
        assert not list((tmp_path / "world/annals").glob("*.md"))

    def test_generation_at_interval(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/annals").mkdir(parents=True)
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        history = [{"tick": i + 1, "laws_count": 0, "population": 1000,
                    "treasury": 0, "era": "Founding Era"} for i in range(10)]  # ticks 1-10
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "# World Annals — Chapter 1\n\nTest content."
        )
        with patch.object(tv, "client", mock_client), \
             patch.object(tv, "run", return_value=""):
            tv.generate_annals(history)
        assert (tmp_path / "world/annals/chapter-001.md").exists()

    def test_no_duplicate_generation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/annals").mkdir(parents=True)
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        chapter = tmp_path / "world/annals/chapter-001.md"
        chapter.write_text("existing content\n")
        history = [{"tick": i + 1, "laws_count": 0, "population": 1000,
                    "treasury": 0} for i in range(10)]  # ticks 1-10 → chapter-001 already exists
        mock_client = MagicMock()
        with patch.object(tv, "client", mock_client), \
             patch.object(tv, "run", return_value=""):
            tv.generate_annals(history)
        mock_client.chat.completions.create.assert_not_called()
        assert chapter.read_text() == "existing content\n"
