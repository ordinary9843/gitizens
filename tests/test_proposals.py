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
    _import_validate,
)


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
        old_date = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%d")
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
# validate_proposal.py — check_cooldown_for_proposal (standalone logic)
# ===========================================================================

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
        old = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%d")
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
# Representative 12h voting period
# ===========================================================================

class TestRepresentativeVotingPeriod:
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

    def _issue(self, age_hours: float, author: str) -> dict:
        created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        return {
            "number": 1, "title": "[PROPOSAL] Test", "createdAt": created,
            "author": {"login": author},
            "body": "## Description\n\nSome law text here.\n\n",
        }

    def test_representative_passes_after_12h(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        (tmp_path / "world/representatives.json").write_text(
            json.dumps({"representatives": ["alice"]}))
        reactions_called = []
        with patch.object(_engine_proposals, "get_reactions",
                          side_effect=lambda n: reactions_called.append(n) or (0, 0, [], [])), \
             patch.object(_engine_proposals, "run", return_value=""):
            _engine_proposals.SKIP_TIMING = False
            tv.process_issue(self._issue(13, "alice"))
            _engine_proposals.SKIP_TIMING = True
        assert reactions_called, "get_reactions should be called — 13h > 12h rep window"

    def test_non_representative_blocked_at_13h(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        (tmp_path / "world/representatives.json").write_text(
            json.dumps({"representatives": ["alice"]}))
        reactions_called = []
        with patch.object(_engine_proposals, "get_reactions",
                          side_effect=lambda n: reactions_called.append(n) or (0, 0, [], [])), \
             patch.object(_engine_proposals, "run", return_value=""):
            _engine_proposals.SKIP_TIMING = False
            tv.process_issue(self._issue(13, "bob"))
            _engine_proposals.SKIP_TIMING = True
        assert not reactions_called, "bob is not a rep — 13h < 24h window, should be skipped"

    def test_non_representative_passes_after_24h(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        (tmp_path / "world/representatives.json").write_text(
            json.dumps({"representatives": ["alice"]}))
        reactions_called = []
        with patch.object(_engine_proposals, "get_reactions",
                          side_effect=lambda n: reactions_called.append(n) or (0, 0, [], [])), \
             patch.object(_engine_proposals, "run", return_value=""):
            _engine_proposals.SKIP_TIMING = False
            tv.process_issue(self._issue(25, "bob"))
            _engine_proposals.SKIP_TIMING = True
        assert reactions_called, "25h > 24h window — non-rep bob should be tallied"
