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

# Engine submodule references for correct patch targeting after sys.path is set
import engine.gh        as _engine_gh
import engine.world     as _engine_world
import engine.events    as _engine_events
import engine.chronicle as _engine_chronicle
import engine.content   as _engine_content
import engine.proposals as _engine_proposals


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
            with patch.object(_engine_world, "read_state", return_value=dict(state)), \
                 patch.object(_engine_world, "write_state") as mock_write:
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
# apply_event_effects
# ===========================================================================

class TestApplyEventEffects:
    def _apply(self, state: dict, effects: dict, key: str = "immediate_effects") -> dict:
        event = {"immediate_effects": effects, "default_consequence": effects,
                 "response_consequence": effects}
        captured = {}
        with patch.object(_engine_world, "read_state", return_value=dict(state)), \
             patch.object(_engine_world, "write_state", side_effect=lambda s: captured.update(s)):
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
    def _make_jsonl(self, items):
        """Return JSONL string matching --jq '.[] | {login, content}' output."""
        return "\n".join(
            json.dumps({"login": i["user"]["login"], "content": i["content"]})
            for i in items
        )

    def test_returns_voter_lists(self):
        raw_items = [
            {"user": {"login": "alice"}, "content": "+1"},
            {"user": {"login": "bob"},   "content": "-1"},
            {"user": {"login": "carol"}, "content": "+1"},
            {"user": {"login": "alice"}, "content": "-1"},  # alice changed her vote
        ]
        with patch.object(_engine_gh, "run", return_value=self._make_jsonl(raw_items)):
            for_c, against_c, for_v, against_v = tv.get_reactions(42)
        # alice's last vote is -1 (dict overwrites)
        assert for_c == 1
        assert against_c == 2
        assert for_v == ["carol"]
        assert sorted(against_v) == ["alice", "bob"]

    def test_empty_reactions(self):
        with patch.object(_engine_gh, "run", return_value=""):
            for_c, against_c, for_v, against_v = tv.get_reactions(99)
        assert for_c == 0 and against_c == 0
        assert for_v == [] and against_v == []

    def test_pagination_multi_page_parsed(self):
        # Simulate paginated output: multiple JSONL blocks (as gh --jq outputs)
        page1 = '{"login": "alice", "content": "+1"}\n{"login": "bob", "content": "+1"}'
        page2 = '{"login": "carol", "content": "-1"}'
        raw = page1 + "\n" + page2
        with patch.object(_engine_gh, "run", return_value=raw):
            for_c, against_c, for_v, against_v = tv.get_reactions(1)
        assert for_c == 2
        assert against_c == 1
        assert for_v == ["alice", "bob"]
        assert against_v == ["carol"]

    def test_malformed_line_skipped(self):
        raw = '{"login": "alice", "content": "+1"}\nNOT_JSON\n{"login": "bob", "content": "+1"}'
        with patch.object(_engine_gh, "run", return_value=raw):
            for_c, against_c, for_v, against_v = tv.get_reactions(1)
        assert for_c == 2
        assert for_v == ["alice", "bob"]


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
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "run", return_value=""):
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
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "run", return_value=""):
            tv.generate_annals(history)
        mock_client.chat.completions.create.assert_not_called()
        assert chapter.read_text() == "existing content\n"


# ===========================================================================
# apply_effect — state_patch allowlist (C1 defense-in-depth)
# ===========================================================================

class TestApplyEffectStatePatch:
    def test_allowed_key_applied(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        effect = {"type": "state_patch", "patch": {"treasury": 500}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 500

    def test_blocked_key_silently_skipped(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        original = {**BASE_STATE, "laws_count": 5}
        (tmp_path / "world/state.json").write_text(json.dumps(original))
        effect = {"type": "state_patch", "patch": {"laws_count": 0, "treasury": 300}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["laws_count"] == 5   # blocked key unchanged
        assert state["treasury"] == 300   # allowed key applied
        assert "BLOCKED" in capsys.readouterr().out

    def test_numeric_value_clamped_to_0_100(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        effect = {"type": "state_patch", "patch": {"education": 999}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["education"] == 100  # clamped

    def test_null_value_skipped(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        original = {**BASE_STATE}
        (tmp_path / "world/state.json").write_text(json.dumps(original))
        effect = {"type": "state_patch", "patch": {"education": None}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["education"] == BASE_STATE["education"]  # unchanged
        assert "BLOCKED" in capsys.readouterr().out

    def test_treasury_capped_at_100000(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        effect = {"type": "state_patch", "patch": {"treasury": 9_999_999}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 100_000


# ===========================================================================
# check_proposal_cooldown — robustness (M1)
# ===========================================================================

class TestProposalCooldownRobustness:
    def test_corrupted_json_file_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/proposal_cooldowns.json").write_text("{ NOT VALID JSON }")
        effect = {"type": "policy", "changes": {"education": 10}}
        ok, _ = tv.check_proposal_cooldown(effect)
        assert ok  # corrupted file → fail open (don't block proposals)

    def test_malformed_date_in_cooldowns_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": "not-a-date"}))
        effect = {"type": "policy", "changes": {"education": 10}}
        ok, _ = tv.check_proposal_cooldown(effect)
        assert ok  # malformed date for metric → skip that metric


# ===========================================================================
# process_feedback — world engine triggered, citizen tracking (H1, H3)
# ===========================================================================

class TestProcessFeedbackWorldEngine:
    def _make_world(self, tmp_path):
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        (tmp_path / "world/citizens.json").write_text("{}")
        (tmp_path / "world/active_event.json").write_text("{}")
        for cat in ("buildings", "districts", "institutions", "sectors"):
            cat_path = tmp_path / "world/entities" / cat
            cat_path.mkdir(parents=True)
            (cat_path / "_index.json").write_text(
                json.dumps({"next_seq": 1, "count": 0, "entities": []}))

    def test_feedback_triggers_world_engine(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        # education at 60; feedback pushes it above 55 threshold → National University
        state = {**BASE_STATE, "education": 53}
        (tmp_path / "world/state.json").write_text(json.dumps(state))
        issue = {
            "number": 42, "title": "[FEEDBACK] Test", "createdAt": "2020-01-01T00:00:00Z",
            "body": "## Description\n\nTest\n\n## Effect\n\n```yaml\ntype: policy\nchanges:\n  education: +5\n```\n",
        }
        engine_calls = []
        with patch.object(_engine_proposals, "get_reactions", return_value=(1, 0, ["alice"], [])), \
             patch.object(_engine_proposals, "run", return_value=""), \
             patch.object(_engine_proposals, "run_world_engine", side_effect=lambda n: engine_calls.append(n) or []):
            _engine_proposals.SKIP_TIMING = True
            tv.process_feedback(issue)
            _engine_proposals.SKIP_TIMING = False
        assert None in engine_calls  # world engine called with None for feedback

    def test_feedback_tracks_citizen_activity(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = {
            "number": 43, "title": "[FEEDBACK] Test", "createdAt": "2020-01-01T00:00:00Z",
            "body": "## Description\n\nTest\n\n## Effect\n\n```yaml\ntype: policy\nchanges:\n  welfare: +1\n```\n",
        }
        with patch.object(_engine_proposals, "get_reactions", return_value=(1, 0, ["bob"], [])), \
             patch.object(_engine_proposals, "run", return_value=""), \
             patch.object(_engine_proposals, "run_world_engine", return_value=[]):
            _engine_proposals.SKIP_TIMING = True
            tv.process_feedback(issue)
            _engine_proposals.SKIP_TIMING = False
        citizens = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "bob" in citizens
        assert citizens["bob"]["total_votes"] == 1

    def test_dismissed_feedback_still_tracks_voters(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = {
            "number": 44, "title": "[FEEDBACK] Test", "createdAt": "2020-01-01T00:00:00Z",
            "body": "anything",
        }
        with patch.object(_engine_proposals, "get_reactions", return_value=(0, 1, [], ["carol"])), \
             patch.object(_engine_proposals, "run", return_value=""):
            _engine_proposals.SKIP_TIMING = True
            result = tv.process_feedback(issue)
            _engine_proposals.SKIP_TIMING = False
        assert result is False
        citizens = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "carol" in citizens  # tracked even on dismissed feedback


# ===========================================================================
# collect_star_income — per-login tracking (anti-star-washing)
# ===========================================================================

class TestCollectStarIncome:
    def _setup(self, tmp_path, state_extra=None):
        (tmp_path / "world").mkdir()
        state = {**BASE_STATE, "treasury": 200, **(state_extra or {})}
        (tmp_path / "world/state.json").write_text(json.dumps(state))
        (tmp_path / "world/stats.json").write_text(json.dumps({}))

    def _run(self, tmp_path, monkeypatch, stargazers_output):
        monkeypatch.chdir(tmp_path)
        calls = []
        def fake_run(cmd):
            if "stargazers" in " ".join(cmd):
                return stargazers_output
            calls.append(cmd)
            return ""
        with patch.object(_engine_chronicle, "run", side_effect=fake_run), \
             patch.object(_engine_chronicle, "generate_dashboard_svg"):
            tv.collect_star_income()
        return calls

    def test_first_run_initializes_no_income(self, tmp_path, monkeypatch):
        self._setup(tmp_path)
        self._run(tmp_path, monkeypatch, "alice\nbob\n")
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 200  # no income on first run
        assert set(state["known_stargazers"]) == {"alice", "bob"}

    def test_new_star_earns_income(self, tmp_path, monkeypatch):
        self._setup(tmp_path, {"known_stargazers": ["alice"]})
        self._run(tmp_path, monkeypatch, "alice\nbob\n")
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 210  # bob is new: +10 GC
        assert set(state["known_stargazers"]) == {"alice", "bob"}

    def test_restar_earns_no_income(self, tmp_path, monkeypatch):
        # alice starred, unstarred (removed from current), then re-starred
        # ever_starred still contains alice → no income
        self._setup(tmp_path, {"known_stargazers": ["alice", "bob"]})
        self._run(tmp_path, monkeypatch, "alice\n")  # bob unstarred, alice re-starred
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 200  # no new income (alice was already known)

    def test_no_stars_no_income(self, tmp_path, monkeypatch):
        self._setup(tmp_path, {"known_stargazers": ["alice"]})
        self._run(tmp_path, monkeypatch, "alice\n")  # same stargazers
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 200

    def test_treasury_capped_at_100000(self, tmp_path, monkeypatch):
        self._setup(tmp_path, {"treasury": 99_995, "known_stargazers": []})
        self._run(tmp_path, monkeypatch, "\n".join(f"u{i}" for i in range(10)))
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 100_000  # capped

    def test_empty_stargazers_no_crash(self, tmp_path, monkeypatch):
        self._setup(tmp_path, {"known_stargazers": ["alice"]})
        self._run(tmp_path, monkeypatch, "")  # no output (0 stars)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 200  # no income, no crash


# ===========================================================================
# append_history_snapshot — tick counter correctness after truncation
# ===========================================================================

class TestAppendHistorySnapshotTick:
    def test_first_tick_is_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        (tmp_path / "world/active_event.json").write_text("{}")
        tv.append_history_snapshot(BASE_STATE)
        hist = json.loads((tmp_path / "world/history.json").read_text())
        assert hist[0]["tick"] == 1

    def test_tick_increments_sequentially(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        (tmp_path / "world/active_event.json").write_text("{}")
        for _ in range(5):
            tv.append_history_snapshot(BASE_STATE)
        hist = json.loads((tmp_path / "world/history.json").read_text())
        assert [h["tick"] for h in hist] == [1, 2, 3, 4, 5]

    def test_tick_continues_after_100_entry_truncation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        (tmp_path / "world/active_event.json").write_text("{}")
        # Seed history with 100 entries (ticks 1–100)
        history = [{"tick": i + 1, "era": "Founding Era", "laws_count": 0,
                    "population": 1000, "treasury": 0, "education": 0,
                    "industry": 0, "welfare": 0, "green_policy": 0, "defense": 0,
                    "pollution": 0, "stability": 79, "active_event": None,
                    "date": "2026-01-01T00:00:00Z"} for i in range(100)]
        (tmp_path / "world/history.json").write_text(json.dumps(history))
        tv.append_history_snapshot(BASE_STATE)
        hist = json.loads((tmp_path / "world/history.json").read_text())
        assert len(hist) == 100       # still capped at 100
        assert hist[-1]["tick"] == 101  # correctly incremented, not reset


# ===========================================================================
# apply_effect evolve — _EVOLVE_BLOCKED defense-in-depth
# ===========================================================================

class TestApplyEffectEvolveBlocked:
    def _make_entity(self, tmp_path, category="buildings", name="bld-001"):
        (tmp_path / "world" / "entities" / category).mkdir(parents=True)
        entity = {
            "id": name, "name": "Test Building",
            "built_law": 3, "built_at": "2026-01-01T00:00:00Z",
            "auto_trigger": "education>=25",
        }
        (tmp_path / f"world/entities/{category}/{name}.json").write_text(json.dumps(entity))

    def test_blocked_field_not_overwritten(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        self._make_entity(tmp_path)
        effect = {"type": "evolve", "id": "bld-001",
                  "changes": {"built_law": 999, "capacity": 500}}
        tv.apply_effect(effect, 10)
        entity = json.loads((tmp_path / "world/entities/buildings/bld-001.json").read_text())
        assert entity["built_law"] == 3    # blocked — unchanged
        assert entity["capacity"] == 500   # allowed — applied

    def test_allowed_field_updated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        self._make_entity(tmp_path)
        effect = {"type": "evolve", "id": "bld-001",
                  "changes": {"level": 2, "description": "Upgraded"}}
        tv.apply_effect(effect, 10)
        entity = json.loads((tmp_path / "world/entities/buildings/bld-001.json").read_text())
        assert entity["level"] == 2
        assert entity["description"] == "Upgraded"
        assert entity["last_evolved_law"] == 10  # always set


# ===========================================================================
# world_autonomous_tick — treasury cap
# ===========================================================================

class TestAutonomousTickTreasuryCap:
    def test_treasury_never_exceeds_100000(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        state = {**BASE_STATE, "treasury": 99_990, "industry": 80, "population": 1000}
        (tmp_path / "world/state.json").write_text(json.dumps(state))
        with patch.object(_engine_world, "write_state") as ws, \
             patch.object(_engine_world, "run", return_value=""):
            tv.world_autonomous_tick()
            written = ws.call_args[0][0]
        assert written["treasury"] <= 100_000


# ===========================================================================
# _state_for_llm — strips large fields before LLM prompts
# ===========================================================================

class TestStateForLlm:
    def test_known_stargazers_stripped(self):
        state = {**BASE_STATE, "known_stargazers": ["alice", "bob", "carol"],
                 "tags_applied": ["era/founding-era"]}
        result = tv._state_for_llm(state)
        assert "known_stargazers" not in result
        assert "tags_applied" not in result

    def test_policy_metrics_preserved(self):
        state = {**BASE_STATE, "known_stargazers": ["x"]}
        result = tv._state_for_llm(state)
        assert result["education"] == BASE_STATE["education"]
        assert result["treasury"] == BASE_STATE["treasury"]

    def test_empty_state_no_crash(self):
        result = tv._state_for_llm({})
        assert result == {}

    def test_original_not_mutated(self):
        state = {**BASE_STATE, "known_stargazers": ["alice"]}
        tv._state_for_llm(state)
        assert "known_stargazers" in state  # original unchanged


# ===========================================================================
# update_readme — STATE_START/END markers
# ===========================================================================

class TestUpdateReadme:
    def test_state_block_replaced(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Header\n\n"
            "<!-- STATE_START -->\nold content\n<!-- STATE_END -->\n\n"
            "Footer",
            encoding="utf-8",
        )
        state = {**BASE_STATE, "era": "Industrial Era", "laws_count": 5,
                 "next_tick_at": "2026-06-12 00:00:00"}
        stats = {"proposals_passed": 3, "proposals_rejected": 1}
        tv.update_readme(state, stats, None, "2026-06-11")
        content = readme.read_text(encoding="utf-8")
        assert "<!-- STATE_START -->" in content
        assert "<!-- STATE_END -->" in content
        assert "old content" not in content
        assert "Industrial Era" in content
        assert "2026-06-12 00:00:00" in content

    def test_missing_markers_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        readme = tmp_path / "README.md"
        original = "# No markers here\n"
        readme.write_text(original, encoding="utf-8")
        state = {**BASE_STATE}
        tv.update_readme(state, {}, None, "2026-06-11")
        assert readme.read_text(encoding="utf-8") == original  # unchanged, no crash


# ===========================================================================
# append_history — FileNotFoundError guard
# ===========================================================================

class TestAppendHistoryGuard:
    def test_missing_index_no_crash(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # history/INDEX.md does NOT exist
        tv.append_history(1, "Test Law", 42, 3, 1, True, "2026-06-11")
        out = capsys.readouterr().out
        assert "WARN" in out  # warning printed, no exception


# ===========================================================================
# update_laws_index — JSONDecodeError guard
# ===========================================================================

class TestUpdateLawsIndexGuard:
    def test_corrupted_json_resets_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("NOT_JSON")
        # Should not raise; instead starts fresh
        tv.update_laws_index(5, "Test", 10, "http://x", "Founding Era", "2026-06-11")
        data = json.loads((tmp_path / "world/laws_index.json").read_text())
        assert len(data) == 1


# ===========================================================================
# _build_chronicle_body — handles missing metrics, includes representatives
# ===========================================================================

class TestBuildChronicleBody:
    def test_empty_dispatches_returns_string(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/dispatches.json").write_text("[]")
        body = tv._build_chronicle_body()
        assert "World Chronicle" in body

    def test_missing_metrics_field_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        # Old-format entry without 'metrics' key
        dispatches = [{"tick": 5, "date": "2026-06-11",
                       "narrative": "Test narrative.", "changes": "tick applied"}]
        (tmp_path / "world/dispatches.json").write_text(json.dumps(dispatches))
        body = tv._build_chronicle_body()
        assert "Test narrative" in body

    def test_includes_last_5_dispatches(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        dispatches = [
            {"tick": i, "date": "2026-06-11", "narrative": f"Narrative {i}.",
             "changes": "tick", "metrics": f"pop {i*100}"}
            for i in range(1, 9)
        ]
        (tmp_path / "world/dispatches.json").write_text(json.dumps(dispatches))
        body = tv._build_chronicle_body()
        # Should contain ticks 4-8 (last 5 of 8), not tick 1
        assert "Narrative 8" in body
        assert "Narrative 4" in body
        assert "Narrative 1" not in body

    def test_includes_representatives_when_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/dispatches.json").write_text("[]")
        reps = {"selected_at": "2026-06-11", "next_selection": "2026-06-18",
                "representatives": ["alice", "bob"]}
        (tmp_path / "world/representatives.json").write_text(json.dumps(reps))
        body = tv._build_chronicle_body()
        assert "@alice" in body
        assert "@bob" in body

    def test_no_representatives_file_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/dispatches.json").write_text("[]")
        # No representatives.json
        body = tv._build_chronicle_body()
        assert "World Chronicle" in body


# ===========================================================================
# upsert_bot_comment — stored ID PATCH path and POST fallback
# ===========================================================================

class TestUpsertBotComment:
    def test_patch_when_id_stored(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/pinned_comment_ids.json").write_text('{"99": 12345}')
        patched = []
        def fake_run(cmd):
            patched.append(cmd)
            if "--method" in cmd and "PATCH" in cmd:
                return '{"id": 12345}'  # non-empty = success
            return ""
        monkeypatch.setattr(_engine_content, "run", fake_run)
        tv.upsert_bot_comment(99, "hello world")
        patch_calls = [c for c in patched if "PATCH" in c]
        assert len(patch_calls) == 1
        assert any("12345" in part for part in patch_calls[0])

    def test_post_when_no_stored_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        # No pinned_comment_ids.json
        posted = []
        def fake_run(cmd):
            if "issue" in cmd and "comment" in cmd:
                posted.append(cmd)
                return "https://github.com/test/repo/issues/5#issuecomment-9876543"
            return ""
        monkeypatch.setattr(_engine_content, "run", fake_run)
        tv.upsert_bot_comment(5, "new comment body")
        assert len(posted) == 1
        ids = json.loads((tmp_path / "world/pinned_comment_ids.json").read_text())
        assert ids.get("5") == 9876543

    def test_patch_failure_falls_back_to_post(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/pinned_comment_ids.json").write_text('{"7": 111}')
        posted = []
        def fake_run(cmd):
            if "PATCH" in cmd:
                return ""  # empty = PATCH failed
            if "issue" in cmd and "comment" in cmd:
                posted.append(cmd)
                return "https://github.com/test/repo/issues/7#issuecomment-222"
            return ""
        monkeypatch.setattr(_engine_content, "run", fake_run)
        tv.upsert_bot_comment(7, "updated body")
        assert len(posted) == 1
        ids = json.loads((tmp_path / "world/pinned_comment_ids.json").read_text())
        assert ids.get("7") == 222


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
# slugify — special character handling
# ===========================================================================

class TestSlugify:
    def test_lowercase_conversion(self):
        assert tv.slugify("Industrial Era") == "industrial-era"

    def test_colon_replaced(self):
        assert tv.slugify("Crisis: Pollution") == "crisis-pollution"

    def test_multiple_specials_collapsed(self):
        assert tv.slugify("Hello   World!!") == "hello-world"

    def test_leading_trailing_stripped(self):
        assert tv.slugify("--hello--") == "hello"

    def test_already_valid(self):
        assert tv.slugify("founding-era") == "founding-era"

    def test_numbers_preserved(self):
        assert tv.slugify("Era 2050") == "era-2050"

    def test_unicode_letters_stripped(self):
        result = tv.slugify("Café Era")
        assert "caf" in result
        assert " " not in result


# ===========================================================================
# check_threshold_tags — milestone crossing detection
# ===========================================================================

class TestCheckThresholdTags:
    def _state(self, **overrides):
        return {**BASE_STATE, "tags_applied": [], **overrides}

    def test_above_crossing_triggers(self):
        before = self._state(welfare=59)
        after  = self._state(welfare=61)
        tags = tv.check_threshold_tags(before, after)
        assert any(t[0] == "milestone/welfare-state" for t in tags)

    def test_above_crossing_updates_tags_applied(self):
        before = self._state(welfare=59)
        after  = self._state(welfare=61)
        tv.check_threshold_tags(before, after)
        assert "milestone/welfare-state" in after["tags_applied"]

    def test_already_applied_not_duplicated(self):
        before = self._state(welfare=59)
        after  = self._state(welfare=61, tags_applied=["milestone/welfare-state"])
        tags = tv.check_threshold_tags(before, after)
        assert not any(t[0] == "milestone/welfare-state" for t in tags)

    def test_below_crossing_triggers(self):
        before = self._state(pollution=25)
        after  = self._state(pollution=15)
        tags = tv.check_threshold_tags(before, after)
        assert any(t[0] == "recovery/air-cleaned" for t in tags)

    def test_no_crossing_no_tag(self):
        before = self._state(welfare=50)
        after  = self._state(welfare=55)
        tags = tv.check_threshold_tags(before, after)
        assert not any(t[0] == "milestone/welfare-state" for t in tags)

    def test_exact_threshold_not_crossed_from_below(self):
        # bv <= threshold < av requires av > threshold; av == threshold doesn't fire
        before = self._state(welfare=59)
        after  = self._state(welfare=60)
        tags = tv.check_threshold_tags(before, after)
        assert not any(t[0] == "milestone/welfare-state" for t in tags)

    def test_exact_threshold_crossed_above(self):
        before = self._state(welfare=60)
        after  = self._state(welfare=61)
        tags = tv.check_threshold_tags(before, after)
        assert any(t[0] == "milestone/welfare-state" for t in tags)

    def test_multiple_tags_fired_in_one_call(self):
        before = self._state(welfare=59, education=49)
        after  = self._state(welfare=61, education=51)
        tags = tv.check_threshold_tags(before, after)
        tag_names = [t[0] for t in tags]
        assert "milestone/welfare-state" in tag_names
        assert "milestone/educated-society" in tag_names

    def test_missing_field_skipped(self):
        before = {}
        after  = {"tags_applied": []}
        tags = tv.check_threshold_tags(before, after)
        assert tags == []


# ===========================================================================
# entity_exists_by_name, auto_create_entity, auto_remove_entity
# ===========================================================================

def _make_category(tmp_path, category: str, entities: list | None = None):
    cat_path = tmp_path / "world" / "entities" / category
    cat_path.mkdir(parents=True)
    entities = entities or []
    index = {"next_seq": len(entities) + 1, "count": len(entities),
             "entities": [e["id"] for e in entities]}
    (cat_path / "_index.json").write_text(json.dumps(index))
    for e in entities:
        (cat_path / f"{e['id']}.json").write_text(json.dumps(e))
    return cat_path


class TestEntityExistsByName:
    def test_finds_existing_entity(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Public School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        assert tv.entity_exists_by_name("buildings", "Public School") == "bld-001"

    def test_case_insensitive_match(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Public School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        assert tv.entity_exists_by_name("buildings", "public school") == "bld-001"
        assert tv.entity_exists_by_name("buildings", "PUBLIC SCHOOL") == "bld-001"

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        assert tv.entity_exists_by_name("buildings", "Nonexistent") is None

    def test_missing_entity_file_skipped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cat_path = tmp_path / "world" / "entities" / "buildings"
        cat_path.mkdir(parents=True)
        index = {"next_seq": 2, "count": 1, "entities": ["bld-001"]}
        (cat_path / "_index.json").write_text(json.dumps(index))
        # bld-001.json intentionally absent
        assert tv.entity_exists_by_name("buildings", "Anything") is None


class TestAutoCreateEntity:
    def test_creates_entity_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        eid = tv.auto_create_entity("buildings", "Test School", 5, "education>=25")
        assert (tmp_path / "world" / "entities" / "buildings" / f"{eid}.json").exists()

    def test_entity_has_correct_fields(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        eid = tv.auto_create_entity("buildings", "Test School", 5, "education>=25")
        data = json.loads(
            (tmp_path / "world/entities/buildings" / f"{eid}.json").read_text())
        assert data["name"] == "Test School"
        assert data["built_law"] == 5
        assert data["auto_trigger"] == "education>=25"

    def test_index_updated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        eid = tv.auto_create_entity("buildings", "Test School", 5, "education>=25")
        idx = json.loads(
            (tmp_path / "world/entities/buildings/_index.json").read_text())
        assert idx["count"] == 1
        assert eid in idx["entities"]

    def test_sequential_ids(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "districts")
        id1 = tv.auto_create_entity("districts", "Park A", 1, "green_policy>=35")
        id2 = tv.auto_create_entity("districts", "Park B", 2, "green_policy>=35")
        assert id1 != id2
        idx = json.loads(
            (tmp_path / "world/entities/districts/_index.json").read_text())
        assert idx["count"] == 2


class TestAutoRemoveEntity:
    def test_entity_file_deleted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Old School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        tv.auto_remove_entity("buildings", "bld-001", 10, "education dropped")
        assert not (tmp_path / "world/entities/buildings/bld-001.json").exists()

    def test_archived_to_world_archive(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Old School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        tv.auto_remove_entity("buildings", "bld-001", 10, "education dropped")
        assert (tmp_path / "world/archive/bld-001.json").exists()

    def test_index_count_decremented(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Old School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        tv.auto_remove_entity("buildings", "bld-001", 10, "education dropped")
        idx = json.loads(
            (tmp_path / "world/entities/buildings/_index.json").read_text())
        assert idx["count"] == 0
        assert "bld-001" not in idx["entities"]

    def test_nonexistent_entity_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        tv.auto_remove_entity("buildings", "bld-999", 5, "reason")

    def test_demolished_fields_in_archive(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Old School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        tv.auto_remove_entity("buildings", "bld-001", 10, "education dropped")
        archived = json.loads((tmp_path / "world/archive/bld-001.json").read_text())
        assert archived["demolished_law"] == 10
        assert "demolished_at" in archived
        assert archived["auto_reason"] == "education dropped"


# ===========================================================================
# run_world_engine — entity creation/removal from policy metrics
# ===========================================================================

class TestRunWorldEngine:
    def _setup_world(self, tmp_path, state_override=None):
        (tmp_path / "world").mkdir()
        state = {**BASE_STATE, **(state_override or {})}
        (tmp_path / "world/state.json").write_text(json.dumps(state))
        for cat in ("buildings", "districts", "institutions", "sectors"):
            _make_category(tmp_path, cat)

    def test_entity_created_when_metric_above_threshold(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, {"education": 55})
        changes = tv.run_world_engine(5)
        assert any("National University" in c for c in changes)
        assert tv.entity_exists_by_name("institutions", "National University") is not None

    def test_no_duplicate_entity_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, {"education": 55})
        tv.run_world_engine(5)
        changes2 = tv.run_world_engine(6)
        assert not any("National University" in c for c in changes2)
        idx = json.loads(
            (tmp_path / "world/entities/institutions/_index.json").read_text())
        assert idx["count"] == 1

    def test_entity_removed_when_metric_drops(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, {"education": 55})
        tv.run_world_engine(5)
        (tmp_path / "world/state.json").write_text(
            json.dumps({**BASE_STATE, "education": 40}))
        changes = tv.run_world_engine(6)
        assert any("National University" in c for c in changes)
        assert tv.entity_exists_by_name("institutions", "National University") is None

    def test_no_change_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # All metrics below all appear-thresholds → nothing created
        self._setup_world(tmp_path, {
            "education": 10, "industry": 10, "welfare": 10,
            "green_policy": 10, "defense": 10, "pollution": 0,
        })
        changes = tv.run_world_engine(1)
        assert changes == []

    def test_pollution_entity_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, {"pollution": 65})
        changes = tv.run_world_engine(7)
        assert any("Smog Zone" in c for c in changes)


# ===========================================================================
# validate_proposal.py — check_cooldown_for_proposal (standalone logic)
# ===========================================================================

import importlib
import os as _os


def _import_validate(tmp_path):
    for k, v in [("ISSUE_NUMBER", "1"), ("ISSUE_TITLE", "[PROPOSAL] Test"),
                 ("ISSUE_BODY", ""), ("GITHUB_TOKEN", "test-token"),
                 ("GITHUB_REPOSITORY", "test/repo")]:
        _os.environ.setdefault(k, v)
        if not _os.environ.get(k):
            _os.environ[k] = v
    import sys
    sys.modules.pop("scripts.validate_proposal", None)
    import scripts.validate_proposal as vp
    return vp


class TestValidateCooldown:
    def _vp(self, tmp_path):
        return _import_validate(tmp_path)

    def test_no_cooldown_file_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 10}})
        assert ok

    def test_cooldown_active_blocks(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": today}))
        ok, reason = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 10}})
        assert not ok
        assert "education" in reason

    def test_cooldown_expired_allows(self, tmp_path, monkeypatch):
        from datetime import datetime, timezone, timedelta
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        old = (datetime.now(timezone.utc) - timedelta(days=15)).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": old}))
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 10}})
        assert ok

    def test_non_policy_always_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal({"type": "declaration"})
        assert ok

    def test_none_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(None)
        assert ok

    def test_corrupted_json_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/proposal_cooldowns.json").write_text("INVALID_JSON")
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 10}})
        assert ok

    def test_malformed_date_skipped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": "not-a-date"}))
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 10}})
        assert ok


# ===========================================================================
# _load_entity_names
# ===========================================================================

class TestLoadEntityNames:
    def test_returns_names_from_all_categories(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Public School", "built_law": 1,
             "built_at": "", "auto_trigger": ""},
        ])
        _make_category(tmp_path, "districts", [
            {"id": "dst-001", "name": "City Park", "built_law": 2,
             "built_at": "", "auto_trigger": ""},
        ])
        _make_category(tmp_path, "institutions")
        _make_category(tmp_path, "sectors")
        names = tv._load_entity_names()
        assert "public school" in names
        assert "city park" in names

    def test_missing_category_dir_skipped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world" / "entities").mkdir(parents=True)
        names = tv._load_entity_names()
        assert names == set()

    def test_names_are_lowercase(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "National University", "built_law": 1,
             "built_at": "", "auto_trigger": ""},
        ])
        _make_category(tmp_path, "districts")
        _make_category(tmp_path, "institutions")
        _make_category(tmp_path, "sectors")
        names = tv._load_entity_names()
        assert "national university" in names
        assert "National University" not in names


# ===========================================================================
# _build_gap_dashboard
# ===========================================================================

class TestBuildGapDashboard:
    def _state(self, **overrides):
        return {**BASE_STATE, "tags_applied": [], **overrides}

    def test_shows_closest_unbuilt_entity(self):
        state = self._state(green_policy=62)
        result = tv._build_gap_dashboard(state, entity_names=set())
        assert "Nature Reserve" in result
        assert "+3" in result

    def test_built_entity_not_shown_as_pending(self):
        state = self._state(green_policy=62)
        result = tv._build_gap_dashboard(state, entity_names={"nature reserve"})
        assert "Nature Reserve" not in result

    def test_at_risk_entity_shown(self):
        state = self._state(green_policy=30)
        result = tv._build_gap_dashboard(state, entity_names={"nature reserve"})
        assert "Nature Reserve" in result
        assert "at risk" in result

    def test_entity_safely_built_not_at_risk(self):
        state = self._state(green_policy=70)
        result = tv._build_gap_dashboard(state, entity_names={"nature reserve"})
        assert "at risk" not in result

    def test_milestone_near_threshold_shown(self):
        state = self._state(industry=44, tags_applied=[])
        result = tv._build_gap_dashboard(state, entity_names=set())
        assert "milestone/industrial-age" in result

    def test_milestone_already_applied_not_shown(self):
        state = self._state(industry=44, tags_applied=["milestone/industrial-age"])
        result = tv._build_gap_dashboard(state, entity_names=set())
        assert "milestone/industrial-age" not in result

    def test_milestone_gap_above_10_not_shown(self):
        state = self._state(industry=30, tags_applied=[])
        result = tv._build_gap_dashboard(state, entity_names=set())
        assert "milestone/industrial-age" not in result

    def test_empty_section_shows_fallback(self):
        all_tag_names = [t[3] for t in tv.THRESHOLD_TAGS]
        # pollution=0 means Smog Zone doesn't exist — exclude it from entity_names
        # so there's no at-risk entity and no pending entity to show
        state = {
            "education": 100, "industry": 100, "welfare": 100,
            "green_policy": 100, "defense": 100, "pollution": 0,
            "stability": 100, "tags_applied": all_tag_names,
        }
        all_entity_names = {r[3].strip().lower() for r in tv.WORLD_GENERATION_RULES
                            if r[0] != "pollution"}
        result = tv._build_gap_dashboard(state, entity_names=all_entity_names)
        assert "All near-threshold goals reached" in result

    def test_shows_at_most_3_pending(self):
        state = {**BASE_STATE, "education": 0, "industry": 0, "welfare": 0,
                 "green_policy": 0, "defense": 0, "pollution": 0,
                 "tags_applied": []}
        result = tv._build_gap_dashboard(state, entity_names=set())
        pending_lines = [line for line in result.splitlines() if "needs +" in line]
        assert len(pending_lines) <= 3

    def test_returns_string_with_header(self):
        result = tv._build_gap_dashboard(self._state(), entity_names=set())
        assert "What Needs Your Vote" in result
        assert isinstance(result, str)


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
