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


# ===========================================================================
# save_proposals_json
# ===========================================================================

class TestSaveProposalsJson:
    def _setup(self, tmp_path):
        (tmp_path / "world").mkdir(parents=True, exist_ok=True)

    def test_writes_minimal_fields(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup(tmp_path)
        api_response = [
            {
                "number": 5,
                "title": "[PROPOSAL] Build Park",
                "html_url": "https://github.com/org/repo/issues/5",
                "created_at": "2026-06-13T00:00:00Z",
                "reactions": {"+1": 3, "-1": 1, "total_count": 4},
            }
        ]
        with patch.object(_engine_proposals, "gh_json", return_value=api_response):
            _engine_proposals.save_proposals_json()
        out = json.loads((tmp_path / "world/proposals.json").read_text())
        assert len(out) == 1
        assert out[0]["number"] == 5
        assert out[0]["reactions"]["+1"] == 3
        assert out[0]["reactions"]["-1"] == 1
        assert set(out[0].keys()) == {"number", "title", "html_url", "created_at", "reactions"}

    def test_handles_empty_response(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup(tmp_path)
        with patch.object(_engine_proposals, "gh_json", return_value=[]):
            _engine_proposals.save_proposals_json()
        out = json.loads((tmp_path / "world/proposals.json").read_text())
        assert out == []

    def test_handles_missing_reactions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup(tmp_path)
        api_response = [
            {
                "number": 7,
                "title": "[PROPOSAL] Tax Cut",
                "html_url": "https://github.com/org/repo/issues/7",
                "created_at": "2026-06-13T00:00:00Z",
            }
        ]
        with patch.object(_engine_proposals, "gh_json", return_value=api_response):
            _engine_proposals.save_proposals_json()
        out = json.loads((tmp_path / "world/proposals.json").read_text())
        assert out[0]["reactions"] == {"+1": 0, "-1": 0}


# ===========================================================================
# parse_effect
# ===========================================================================

class TestParseEffect:
    def test_parses_yaml_block(self):
        body = "## Effect\n\n```yaml\ntype: policy\nchanges:\n  education: 10\n```\n"
        result = _engine_proposals.parse_effect(body)
        assert result == {"type": "policy", "changes": {"education": 10}}

    def test_parses_yml_fence(self):
        body = "## Effect\n\n```yml\ntype: state_patch\npatch:\n  treasury: 500\n```\n"
        result = _engine_proposals.parse_effect(body)
        assert result == {"type": "state_patch", "patch": {"treasury": 500}}

    def test_no_effect_block_returns_none(self):
        body = "## Description\n\nJust some proposal text with no effect block."
        result = _engine_proposals.parse_effect(body)
        assert result is None

    def test_empty_body_returns_none(self):
        result = _engine_proposals.parse_effect("")
        assert result is None

    def test_non_dict_yaml_returns_none(self):
        body = "## Effect\n\n```yaml\n- item1\n- item2\n```\n"
        result = _engine_proposals.parse_effect(body)
        assert result is None

    def test_invalid_yaml_returns_none(self):
        body = "## Effect\n\n```yaml\n: bad: yaml: {{\n```\n"
        result = _engine_proposals.parse_effect(body)
        assert result is None

    def test_multiline_yaml_parses_correctly(self):
        body = (
            "## Effect\n\n```yaml\n"
            "type: policy\n"
            "changes:\n"
            "  welfare: 5\n"
            "  education: -3\n"
            "```\n"
        )
        result = _engine_proposals.parse_effect(body)
        assert result["changes"]["welfare"] == 5
        assert result["changes"]["education"] == -3


# ===========================================================================
# next_law_number
# ===========================================================================

class TestNextLawNumber:
    def test_returns_one_when_laws_count_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({"era": "Founding Era"}))
        result = _engine_proposals.next_law_number()
        assert result == 1

    def test_increments_from_existing_count(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({"laws_count": 7}))
        result = _engine_proposals.next_law_number()
        assert result == 8

    def test_returns_one_when_laws_count_is_zero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({"laws_count": 0}))
        result = _engine_proposals.next_law_number()
        assert result == 1


# ===========================================================================
# get_open_proposals / get_ai_proposals / get_feedbacks
# ===========================================================================

class TestGetOpenProposals:
    def test_returns_sorted_list(self):
        raw = [
            {"number": 5, "title": "[PROPOSAL] B", "body": "", "createdAt": "2026-01-02T00:00:00Z", "author": {"login": "bob"}},
            {"number": 2, "title": "[PROPOSAL] A", "body": "", "createdAt": "2026-01-01T00:00:00Z", "author": {"login": "alice"}},
        ]
        with patch.object(_engine_proposals, "gh_json", return_value=raw):
            result = _engine_proposals.get_open_proposals()
        assert result[0]["number"] == 2
        assert result[1]["number"] == 5

    def test_calls_gh_json_with_proposal_label(self):
        with patch.object(_engine_proposals, "gh_json", return_value=[]) as mock_gh:
            _engine_proposals.get_open_proposals()
        args = mock_gh.call_args[0][0]
        assert "proposal" in args
        assert "open" in args

    def test_returns_empty_list_when_no_issues(self):
        with patch.object(_engine_proposals, "gh_json", return_value=[]):
            result = _engine_proposals.get_open_proposals()
        assert result == []


class TestGetAiProposals:
    def test_returns_sorted_list(self):
        raw = [
            {"number": 10, "title": "[AI-PROPOSAL] Z", "body": "", "createdAt": "2026-01-03T00:00:00Z"},
            {"number": 3,  "title": "[AI-PROPOSAL] A", "body": "", "createdAt": "2026-01-01T00:00:00Z"},
        ]
        with patch.object(_engine_proposals, "gh_json", return_value=raw):
            result = _engine_proposals.get_ai_proposals()
        assert result[0]["number"] == 3
        assert result[1]["number"] == 10

    def test_calls_gh_json_with_ai_proposal_label(self):
        with patch.object(_engine_proposals, "gh_json", return_value=[]) as mock_gh:
            _engine_proposals.get_ai_proposals()
        args = mock_gh.call_args[0][0]
        assert "ai-proposal" in args

    def test_returns_empty_when_no_ai_proposals(self):
        with patch.object(_engine_proposals, "gh_json", return_value=[]):
            result = _engine_proposals.get_ai_proposals()
        assert result == []


class TestGetFeedbacks:
    def test_returns_sorted_list(self):
        raw = [
            {"number": 8, "title": "[FEEDBACK] C", "body": "", "createdAt": "2026-01-03T00:00:00Z"},
            {"number": 1, "title": "[FEEDBACK] A", "body": "", "createdAt": "2026-01-01T00:00:00Z"},
        ]
        with patch.object(_engine_proposals, "gh_json", return_value=raw):
            result = _engine_proposals.get_feedbacks()
        assert result[0]["number"] == 1
        assert result[1]["number"] == 8

    def test_calls_gh_json_with_feedback_label(self):
        with patch.object(_engine_proposals, "gh_json", return_value=[]) as mock_gh:
            _engine_proposals.get_feedbacks()
        args = mock_gh.call_args[0][0]
        assert "feedback" in args

    def test_returns_empty_when_no_feedbacks(self):
        with patch.object(_engine_proposals, "gh_json", return_value=[]):
            result = _engine_proposals.get_feedbacks()
        assert result == []


# ===========================================================================
# process_issue — happy path, rejection, zero votes guard
# ===========================================================================

class TestProcessIssue:
    def _make_world(self, tmp_path):
        (tmp_path / "world").mkdir(parents=True, exist_ok=True)
        (tmp_path / "world/laws").mkdir(parents=True, exist_ok=True)
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        (tmp_path / "world/stats.json").write_text(json.dumps({}))
        (tmp_path / "world/citizens.json").write_text("{}")
        (tmp_path / "world/active_event.json").write_text("{}")
        (tmp_path / "world/history.json").write_text("[]")
        (tmp_path / "world/laws_index.json").write_text("[]")
        for cat in ("buildings", "districts", "institutions", "sectors"):
            cat_path = tmp_path / "world" / "entities" / cat
            cat_path.mkdir(parents=True)
            (cat_path / "_index.json").write_text(
                json.dumps({"next_seq": 1, "count": 0, "entities": []}))

    def _issue(self, age_hours=48, for_votes=3, against_votes=1,
               title="[PROPOSAL] Build Roads", body=""):
        created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        return {
            "number": 99,
            "title": title,
            "body": body,
            "createdAt": created,
            "author": {"login": "alice"},
        }

    def _common_patches(self, for_votes, against_votes, for_voters=None, against_voters=None):
        if for_voters is None:
            for_voters = [f"voter{i}" for i in range(for_votes)]
        if against_voters is None:
            against_voters = [f"oppvoter{i}" for i in range(against_votes)]
        return (
            patch.object(_engine_proposals, "get_reactions",
                         return_value=(for_votes, against_votes, for_voters, against_voters)),
            patch.object(_engine_proposals, "run", return_value=""),
            patch.object(_engine_proposals, "generate_narrative", return_value="Test narrative."),
            patch.object(_engine_proposals, "generate_world_md", return_value=None),
            patch.object(_engine_proposals, "update_readme", return_value=None),
            patch.object(_engine_proposals, "append_history", return_value=None),
            patch.object(_engine_proposals, "update_laws_index", return_value=None),
            patch.object(_engine_proposals, "apply_tags", return_value=None),
            patch.object(_engine_proposals, "run_world_engine", return_value=[]),
        )

    def test_passed_proposal_updates_state(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = self._issue(for_votes=3, against_votes=1)
        patches = self._common_patches(3, 1)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], \
             patch.object(_engine_proposals, "update_world_summary", return_value="Test summary."):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_issue(issue)
            _engine_proposals.SKIP_TIMING = False
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["laws_count"] == BASE_STATE["laws_count"] + 1
        assert state["last_enacted"] is not None

    def test_rejected_proposal_increments_rejected_stats(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = self._issue(for_votes=1, against_votes=3)
        patches = self._common_patches(1, 3)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8]:
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_issue(issue)
            _engine_proposals.SKIP_TIMING = False
        stats = json.loads((tmp_path / "world/stats.json").read_text())
        assert stats.get("proposals_rejected", 0) >= 1
        assert stats.get("proposals_total", 0) >= 1

    def test_zero_votes_closes_silently(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = self._issue(for_votes=0, against_votes=0)
        run_calls = []
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(0, 0, [], [])), \
             patch.object(_engine_proposals, "run",
                          side_effect=lambda cmd, **kw: run_calls.append(cmd) or ""):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_issue(issue)
            _engine_proposals.SKIP_TIMING = False
        closed = any("close" in cmd for cmd in run_calls)
        assert closed, "Zero-vote proposal should be closed"
        stats = json.loads((tmp_path / "world/stats.json").read_text())
        assert stats.get("proposals_silent", 0) >= 1

    def test_passed_proposal_law_file_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = self._issue(for_votes=5, against_votes=0)
        patches = self._common_patches(5, 0)
        with patches[0], patches[1], patches[2], patches[3], patches[4], \
             patches[5], patches[6], patches[7], patches[8], \
             patch.object(_engine_proposals, "update_world_summary", return_value="Test summary."):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_issue(issue)
            _engine_proposals.SKIP_TIMING = False
        law_num = BASE_STATE["laws_count"] + 1
        law_file = tmp_path / "world" / "laws" / f"law-{law_num:03d}.md"
        assert law_file.exists()
        content = law_file.read_text(encoding="utf-8")
        assert "Build Roads" in content


# ===========================================================================
# process_ai_proposal — enacted, vetoed, cooldown guard
# ===========================================================================

class TestProcessAiProposal:
    def _make_world(self, tmp_path):
        (tmp_path / "world").mkdir(parents=True, exist_ok=True)
        (tmp_path / "world/laws").mkdir(parents=True, exist_ok=True)
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        (tmp_path / "world/stats.json").write_text(json.dumps({}))
        (tmp_path / "world/citizens.json").write_text("{}")
        (tmp_path / "world/active_event.json").write_text("{}")
        (tmp_path / "world/history.json").write_text("[]")
        (tmp_path / "world/laws_index.json").write_text("[]")
        for cat in ("buildings", "districts", "institutions", "sectors"):
            cat_path = tmp_path / "world" / "entities" / cat
            cat_path.mkdir(parents=True)
            (cat_path / "_index.json").write_text(
                json.dumps({"next_seq": 1, "count": 0, "entities": []}))

    def _ai_issue(self, age_hours=6, against_votes=0, body=""):
        created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        return {
            "number": 55,
            "title": "[AI-PROPOSAL] Expand Gardens",
            "body": body,
            "createdAt": created,
        }

    def test_enacted_when_no_veto(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = self._ai_issue(against_votes=0)
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(0, 0, [], [])), \
             patch.object(_engine_proposals, "run", return_value=""), \
             patch.object(_engine_proposals, "generate_narrative", return_value="AI narrative."), \
             patch.object(_engine_proposals, "update_world_summary", return_value="AI summary."), \
             patch.object(_engine_proposals, "generate_world_md", return_value=None), \
             patch.object(_engine_proposals, "update_readme", return_value=None), \
             patch.object(_engine_proposals, "append_history", return_value=None), \
             patch.object(_engine_proposals, "update_laws_index", return_value=None), \
             patch.object(_engine_proposals, "apply_tags", return_value=None), \
             patch.object(_engine_proposals, "run_world_engine", return_value=[]), \
             patch.object(_engine_proposals, "generate_dashboard_svg", return_value=None,
                          create=True):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_ai_proposal(issue)
            _engine_proposals.SKIP_TIMING = False
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["laws_count"] == BASE_STATE["laws_count"] + 1

    def test_vetoed_when_against_votes_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = self._ai_issue(against_votes=2)
        run_calls = []
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(0, 2, [], ["bob", "carol"])), \
             patch.object(_engine_proposals, "run",
                          side_effect=lambda cmd, **kw: run_calls.append(cmd) or ""):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_ai_proposal(issue)
            _engine_proposals.SKIP_TIMING = False
        closed = any("close" in cmd for cmd in run_calls)
        assert closed, "Vetoed AI proposal should be closed"
        stats = json.loads((tmp_path / "world/stats.json").read_text())
        assert stats.get("proposals_rejected", 0) >= 1

    def test_blocked_by_cooldown(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": today}))
        body = (
            "## Effect\n\n```yaml\n"
            "type: policy\n"
            "changes:\n"
            "  education: 5\n"
            "```\n"
        )
        issue = self._ai_issue(against_votes=0, body=body)
        run_calls = []
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(0, 0, [], [])), \
             patch.object(_engine_proposals, "run",
                          side_effect=lambda cmd, **kw: run_calls.append(cmd) or ""):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_ai_proposal(issue)
            _engine_proposals.SKIP_TIMING = False
        closed = any("close" in cmd for cmd in run_calls)
        assert closed, "Cooldown-blocked AI proposal should be closed"


# ===========================================================================
# process_feedback — pass, reject, no-effect path
# ===========================================================================

class TestProcessFeedbackBasic:
    def _make_world(self, tmp_path):
        (tmp_path / "world").mkdir(parents=True, exist_ok=True)
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        (tmp_path / "world/citizens.json").write_text("{}")
        (tmp_path / "world/active_event.json").write_text("{}")
        for cat in ("buildings", "districts", "institutions", "sectors"):
            cat_path = tmp_path / "world" / "entities" / cat
            cat_path.mkdir(parents=True)
            (cat_path / "_index.json").write_text(
                json.dumps({"next_seq": 1, "count": 0, "entities": []}))

    def _feedback_issue(self, age_hours=6, body=""):
        created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        return {
            "number": 77,
            "title": "[FEEDBACK] Lower taxes",
            "body": body,
            "createdAt": created,
        }

    def test_feedback_applied_returns_true(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = self._feedback_issue(body="No mechanical effect here.")
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(1, 0, ["alice"], [])), \
             patch.object(_engine_proposals, "run", return_value=""), \
             patch.object(_engine_proposals, "run_world_engine", return_value=[]):
            _engine_proposals.SKIP_TIMING = True
            result = _engine_proposals.process_feedback(issue)
            _engine_proposals.SKIP_TIMING = False
        assert result is True

    def test_feedback_dismissed_returns_false(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        issue = self._feedback_issue(body="Some feedback.")
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(0, 2, [], ["bob", "carol"])), \
             patch.object(_engine_proposals, "run", return_value=""):
            _engine_proposals.SKIP_TIMING = True
            result = _engine_proposals.process_feedback(issue)
            _engine_proposals.SKIP_TIMING = False
        assert result is False

    def test_feedback_with_policy_effect_updates_state(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        body = (
            "## Effect\n\n```yaml\n"
            "type: policy\n"
            "changes:\n"
            "  welfare: 5\n"
            "```\n"
        )
        issue = self._feedback_issue(body=body)
        initial_welfare = BASE_STATE["welfare"]
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(1, 0, ["alice"], [])), \
             patch.object(_engine_proposals, "run", return_value=""), \
             patch.object(_engine_proposals, "run_world_engine", return_value=[]):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_feedback(issue)
            _engine_proposals.SKIP_TIMING = False
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["welfare"] == min(100, initial_welfare + 5)
