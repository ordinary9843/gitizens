import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

from tests.helpers import (
    BASE_STATE, tv,
    _engine_gh, _engine_world, _engine_events,
    _engine_chronicle, _engine_content, _engine_proposals,
    _make_category,
)
from engine.events import fire_chained_event, open_event_issue, close_event_issue


# ===========================================================================
# fire_random_event — LLM-delegating implementation
# ===========================================================================

class TestEventEligibility:
    def test_no_trigger_above_threshold(self):
        state = {**BASE_STATE}
        with patch("engine.events.random.random", return_value=0.16):
            result = tv.fire_random_event(state)
            assert result is None

    def test_no_trigger_at_boundary(self):
        state = {**BASE_STATE}
        with patch("engine.events.random.random", return_value=0.16):
            result = tv.fire_random_event(state)
            assert result is None

    def test_triggers_and_returns_generate_event_result(self):
        expected = {"id": "evt-llm-1", "title": "Storm", "category": "natural"}
        with patch("engine.events.random.random", return_value=0.05):
            with patch("engine.events.generate_event", return_value=expected) as mock_gen:
                result = tv.fire_random_event(BASE_STATE)
        assert result == expected
        mock_gen.assert_called_once_with(BASE_STATE)

    def test_returns_none_when_generate_event_returns_none(self):
        with patch("engine.events.random.random", return_value=0.05):
            with patch("engine.events.generate_event", return_value=None):
                result = tv.fire_random_event(BASE_STATE)
        assert result is None

    def test_probability_threshold_is_15_percent(self):
        # random() == 0.15 means random() > 0.15 is False, so event fires
        with patch("engine.events.random.random", return_value=0.15):
            with patch("engine.events.generate_event", return_value={"id": "x"}) as mock_gen:
                tv.fire_random_event(BASE_STATE)
        mock_gen.assert_called_once()

    def test_random_above_threshold_skips_generate(self):
        with patch("engine.events.random.random", return_value=0.9):
            with patch("engine.events.generate_event") as mock_gen:
                tv.fire_random_event(BASE_STATE)
        mock_gen.assert_not_called()


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
    def _resolved(self):
        return {
            "id": "evt-001",
            "title": "Flood",
            "category": "natural",
            "immediate_effects": {"welfare": -10},
            "response_consequence": {"treasury": 5},
            "default_consequence": {"treasury": -5},
            "duration_hours": 4,
            "issue_number": 42,
        }

    def test_chain_fires_when_llm_returns_event(self):
        chained = {
            "id": "evt-chain-001",
            "title": "Rescue Effort",
            "category": "social",
            "rarity": "common",
            "description": "Citizens rally.",
            "flavor": "",
            "immediate_effects": {"welfare": 5},
            "response_consequence": {},
            "default_consequence": {},
            "response_hint": "",
            "duration_hours": 4,
            "chained_from": "evt-001",
        }
        with patch("engine.events.generate_chained_event", return_value=chained) as mock_gen:
            with patch("engine.events.load_active_event", return_value=None):
                with patch("engine.events.read_state", return_value={"treasury": 200}):
                    with patch("engine.events.apply_event_effects"):
                        with patch("engine.events.open_event_issue", return_value=99):
                            with patch("engine.events.save_active_event") as mock_save:
                                fire_chained_event(self._resolved(), True)
        mock_gen.assert_called_once()
        mock_save.assert_called_once()

    def test_chain_skipped_when_llm_returns_none(self):
        with patch("engine.events.generate_chained_event", return_value=None):
            with patch("engine.events.load_active_event", return_value=None):
                with patch("engine.events.read_state", return_value={}):
                    with patch("engine.events.save_active_event") as mock_save:
                        fire_chained_event(self._resolved(), False)
        mock_save.assert_not_called()

    def test_no_chain_when_event_already_active(self):
        with patch("engine.events.load_active_event", return_value={"id": "active"}):
            with patch("engine.events.generate_chained_event") as mock_gen:
                fire_chained_event(self._resolved(), True)
        mock_gen.assert_not_called()

    def test_chain_response_true_passed_to_generator(self):
        with patch("engine.events.generate_chained_event", return_value=None) as mock_gen:
            with patch("engine.events.load_active_event", return_value=None):
                with patch("engine.events.read_state", return_value={}):
                    fire_chained_event(self._resolved(), True)
        args = mock_gen.call_args
        assert args[0][1] is True  # responded=True passed through


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
# fire_random_event — probability threshold edge cases
# ===========================================================================

class TestCategoryMultipliers:
    """Verify the 15% probability gate is the sole logic in fire_random_event."""

    def test_exactly_015_triggers_event(self):
        # random() == 0.15 means 0.15 > 0.15 is False, so the event fires
        with patch("engine.events.random.random", return_value=0.15):
            with patch("engine.events.generate_event", return_value={"id": "x"}) as mock_gen:
                tv.fire_random_event(BASE_STATE)
        mock_gen.assert_called_once()

    def test_just_above_015_skips_event(self):
        with patch("engine.events.random.random", return_value=0.151):
            with patch("engine.events.generate_event") as mock_gen:
                result = tv.fire_random_event(BASE_STATE)
        assert result is None
        mock_gen.assert_not_called()

    def test_zero_always_triggers(self):
        with patch("engine.events.random.random", return_value=0.0):
            with patch("engine.events.generate_event", return_value={"id": "y"}) as mock_gen:
                tv.fire_random_event(BASE_STATE)
        mock_gen.assert_called_once()

    def test_generate_event_receives_full_state(self):
        state = {**BASE_STATE, "treasury": 999}
        with patch("engine.events.random.random", return_value=0.05):
            with patch("engine.events.generate_event", return_value=None) as mock_gen:
                tv.fire_random_event(state)
        mock_gen.assert_called_once_with(state)


# ===========================================================================
# open_event_issue
# ===========================================================================

class TestOpenEventIssue:
    _BASE_EVENT = {
        "title": "Great Flood",
        "description": "Waters rise across the lowlands.",
        "flavor": "The rivers speak.",
        "category": "natural",
        "rarity": "rare",
        "immediate_effects": {"welfare": -10, "treasury": -5},
        "response_consequence": {"stability": 10},
        "default_consequence": {"stability": -5},
        "response_hint": "React to mobilise rescue teams.",
    }

    def test_returns_issue_number_from_url(self):
        # run() returns a GitHub issue URL; the function extracts the last segment.
        with patch("engine.events.run", return_value="https://github.com/test/repo/issues/77"):
            result = open_event_issue(self._BASE_EVENT)
        assert result == 77

    def test_calls_gh_issue_create_with_title(self):
        with patch("engine.events.run", return_value="https://github.com/test/repo/issues/10") as mock_run:
            open_event_issue(self._BASE_EVENT)
        # At least one call should contain the issue title with [EVENT] prefix.
        all_calls = [str(c) for c in mock_run.call_args_list]
        assert any("[EVENT] Great Flood" in c for c in all_calls)

    def test_calls_gh_issue_create_with_event_label(self):
        with patch("engine.events.run", return_value="https://github.com/test/repo/issues/10") as mock_run:
            open_event_issue(self._BASE_EVENT)
        all_calls_flat = [arg for c in mock_run.call_args_list for arg in c[0][0]]
        assert "event" in all_calls_flat

    def test_body_contains_description(self):
        captured_bodies = []

        def fake_run(cmd):
            # Capture body file path from --body-file arg.
            if "--body-file" in cmd:
                idx = cmd.index("--body-file")
                body_path = cmd[idx + 1]
                with open(body_path, encoding="utf-8") as f:
                    captured_bodies.append(f.read())
            return "https://github.com/test/repo/issues/5"

        with patch("engine.events.run", side_effect=fake_run):
            open_event_issue(self._BASE_EVENT)

        assert len(captured_bodies) == 1
        assert "Waters rise across the lowlands." in captured_bodies[0]
        assert "Great Flood" in captured_bodies[0]

    def test_returns_zero_on_unparseable_url(self):
        with patch("engine.events.run", return_value="not-a-url"):
            result = open_event_issue(self._BASE_EVENT)
        assert result == 0

    def test_returns_zero_on_empty_run_output(self):
        with patch("engine.events.run", return_value=""):
            result = open_event_issue(self._BASE_EVENT)
        assert result == 0

    def test_event_with_no_immediate_effects(self):
        event = {**self._BASE_EVENT, "immediate_effects": {}}
        with patch("engine.events.run", return_value="https://github.com/test/repo/issues/3"):
            result = open_event_issue(event)
        assert result == 3


# ===========================================================================
# close_event_issue
# ===========================================================================

class TestCloseEventIssue:
    _BASE_EVENT = {
        "title": "Great Flood",
        "description": "Waters rise.",
        "response_consequence": {"stability": 10, "treasury": 5},
        "default_consequence": {"stability": -5},
    }

    def test_calls_comment_close_and_remove_label(self):
        with patch("engine.events.run") as mock_run:
            close_event_issue(42, True, self._BASE_EVENT)
        assert mock_run.call_count == 3
        cmds = [c[0][0] for c in mock_run.call_args_list]
        # First: comment; second: close; third: remove label
        assert "comment" in cmds[0]
        assert "close" in cmds[1]
        assert "edit" in cmds[2]

    def test_passes_correct_issue_number(self):
        with patch("engine.events.run") as mock_run:
            close_event_issue(99, False, self._BASE_EVENT)
        all_calls_flat = [arg for c in mock_run.call_args_list for arg in c[0][0]]
        assert "99" in all_calls_flat

    def test_early_return_when_issue_number_is_zero(self):
        with patch("engine.events.run") as mock_run:
            close_event_issue(0, True, self._BASE_EVENT)
        mock_run.assert_not_called()

    def test_responded_true_applies_response_consequence(self):
        captured = []

        def fake_run(cmd):
            captured.append(cmd)

        with patch("engine.events.run", side_effect=fake_run):
            close_event_issue(42, True, self._BASE_EVENT)

        # The comment body (third arg of the comment call) should mention response effects.
        comment_cmd = captured[0]
        body_idx = comment_cmd.index("--body")
        body_text = comment_cmd[body_idx + 1]
        assert "stability" in body_text
        assert "treasury" in body_text

    def test_responded_false_applies_default_consequence(self):
        captured = []

        def fake_run(cmd):
            captured.append(cmd)

        with patch("engine.events.run", side_effect=fake_run):
            close_event_issue(42, False, self._BASE_EVENT)

        comment_cmd = captured[0]
        body_idx = comment_cmd.index("--body")
        body_text = comment_cmd[body_idx + 1]
        assert "stability" in body_text

    def test_repo_flag_present_in_all_calls(self):
        with patch("engine.events.run") as mock_run:
            close_event_issue(10, True, self._BASE_EVENT)
        for c in mock_run.call_args_list:
            cmd = c[0][0]
            assert "--repo" in cmd
