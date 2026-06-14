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

    def test_empty_line_in_output_skipped(self):
        raw = '{"login": "alice", "content": "+1"}\n\n{"login": "bob", "content": "-1"}'
        with patch.object(_engine_gh, "run", return_value=raw):
            for_c, against_c, for_v, against_v = tv.get_reactions(1)
        assert for_c == 1 and against_c == 1
        assert for_v == ["alice"] and against_v == ["bob"]


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
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok
        assert extra == 0

    def test_cooldown_active_blocks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": {"last_date": today, "streak": 1}}))
        effect = {"type": "policy", "changes": {"education": 10}}
        ok, reason, extra = tv.check_proposal_cooldown(effect)
        assert not ok
        assert "education" in reason
        assert extra == 0  # blocked proposals report no extra cost

    def test_cooldown_expired_allows(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        # 4 days back is well outside the 1-day hard block but within the
        # 7-day surcharge window, so streak penalty applies.
        old_date = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": {"last_date": old_date, "streak": 1}}))
        effect = {"type": "policy", "changes": {"education": 10}}
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok
        assert extra == 100  # streak 1 -> next = 2 -> penalty 100

    def test_non_policy_always_ok(self):
        ok, _, extra = tv.check_proposal_cooldown({"type": "declaration"})
        assert ok
        assert extra == 0

    def test_none_effect_data_always_ok(self):
        ok, _, extra = tv.check_proposal_cooldown(None)
        assert ok
        assert extra == 0

    def test_update_writes_record(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        effect = {"type": "policy", "changes": {"welfare": 5, "education": 3}}
        tv.update_proposal_cooldown(effect, "2026-06-11")
        data = json.loads((tmp_path / "world/proposal_cooldowns.json").read_text())
        assert data["welfare"]   == {"last_date": "2026-06-11", "streak": 1}
        assert data["education"] == {"last_date": "2026-06-11", "streak": 1}

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
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok  # corrupted file -> fail open (don't block proposals)
        assert extra == 0

    def test_malformed_date_in_cooldowns_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": {"last_date": "not-a-date", "streak": 1}}))
        effect = {"type": "policy", "changes": {"education": 10}}
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok  # malformed date for metric -> skip that metric
        assert extra == 0


# ===========================================================================
# check_proposal_cooldown — streak & legacy migration (Item 6)
# ===========================================================================

class TestCooldownStreaks:
    def _write(self, tmp_path, payload):
        (tmp_path / "world").mkdir(exist_ok=True)
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps(payload), encoding="utf-8")

    def test_legacy_string_record_migrates_on_read(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        old_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        self._write(tmp_path, {"education": old_date})  # old `{metric: date}` shape
        effect = {"type": "policy", "changes": {"education": 5}}
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok
        # Migrated record gets streak=1; touching it would make it streak=2 -> penalty 100.
        assert extra == 100

    def test_streak_penalty_doubles(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        old_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        # streak 4 stored -> next would be 5 -> penalty 100 * 2^(5-2) = 800
        self._write(tmp_path, {"welfare": {"last_date": old_date, "streak": 4}})
        effect = {"type": "policy", "changes": {"welfare": 3}}
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok
        assert extra == 800

    def test_penalty_resets_after_decay_window(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # 8 days back is past the 7-day decay window -> no surcharge on this touch.
        old_date = (datetime.now(timezone.utc) - timedelta(days=8)).strftime("%Y-%m-%d")
        self._write(tmp_path, {"welfare": {"last_date": old_date, "streak": 5}})
        effect = {"type": "policy", "changes": {"welfare": 3}}
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok
        assert extra == 0

    def test_update_increments_streak_within_window(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        old_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        self._write(tmp_path, {"welfare": {"last_date": old_date, "streak": 2}})
        effect = {"type": "policy", "changes": {"welfare": 3}}
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tv.update_proposal_cooldown(effect, today)
        data = json.loads((tmp_path / "world/proposal_cooldowns.json").read_text())
        assert data["welfare"]["streak"] == 3
        assert data["welfare"]["last_date"] == today

    def test_update_resets_streak_after_decay(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y-%m-%d")
        self._write(tmp_path, {"welfare": {"last_date": old_date, "streak": 5}})
        effect = {"type": "policy", "changes": {"welfare": 3}}
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        tv.update_proposal_cooldown(effect, today)
        data = json.loads((tmp_path / "world/proposal_cooldowns.json").read_text())
        assert data["welfare"]["streak"] == 1

    def test_multiple_metrics_accumulate_extra_cost(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        old_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        self._write(tmp_path, {
            "welfare":   {"last_date": old_date, "streak": 1},  # next=2 -> 100
            "education": {"last_date": old_date, "streak": 2},  # next=3 -> 200
        })
        effect = {"type": "policy", "changes": {"welfare": 1, "education": 1}}
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok
        assert extra == 300

    def test_independent_streaks_per_metric(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        old_date = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        self._write(tmp_path, {"welfare": {"last_date": old_date, "streak": 3}})
        # Updating education doesn't affect welfare's streak.
        effect = {"type": "policy", "changes": {"education": 1}}
        tv.update_proposal_cooldown(effect, datetime.now(timezone.utc).strftime("%Y-%m-%d"))
        data = json.loads((tmp_path / "world/proposal_cooldowns.json").read_text())
        assert data["welfare"]["streak"]   == 3
        assert data["education"]["streak"] == 1

    def test_hard_block_within_one_day(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._write(tmp_path, {"welfare": {"last_date": today, "streak": 2}})
        effect = {"type": "policy", "changes": {"welfare": 1}}
        ok, reason, _ = tv.check_proposal_cooldown(effect)
        assert not ok
        assert "welfare" in reason

    def test_empty_changes_returns_no_cost(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        effect = {"type": "policy", "changes": {}}
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok
        assert extra == 0

    def test_update_with_invalid_date_string_falls_back_to_today(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        effect = {"type": "policy", "changes": {"welfare": 1}}
        # Garbage date string in caller -> function falls back to today and persists it.
        tv.update_proposal_cooldown(effect, "not-a-date")
        data = json.loads((tmp_path / "world/proposal_cooldowns.json").read_text())
        assert data["welfare"]["last_date"] == "not-a-date"  # stored verbatim
        assert data["welfare"]["streak"] == 1

    def test_legacy_list_payload_is_ignored(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Defensive: a top-level list (clearly corrupted shape) -> treat as empty.
        (tmp_path / "world").mkdir()
        (tmp_path / "world/proposal_cooldowns.json").write_text(json.dumps([]))
        effect = {"type": "policy", "changes": {"welfare": 1}}
        ok, _, extra = tv.check_proposal_cooldown(effect)
        assert ok
        assert extra == 0


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
             patch.object(_engine_proposals, "run_world_engine", return_value=[]):
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


# ===========================================================================
# process_ai_proposal — atomic close before post-processing (tick-reliability fix)
# ===========================================================================

class TestAiProposalAtomicClose:

    def test_no_generate_dashboard_svg_call(self):
        """generate_dashboard_svg must not appear anywhere in proposals module source."""
        import scripts.engine.proposals as p
        src = open(p.__file__, encoding="utf-8").read()
        assert "generate_dashboard_svg" not in src

    def test_issue_closed_before_generate_world_md(self, tmp_path, monkeypatch):
        """Issue close/relabel must happen before generate_world_md is called."""
        import scripts.engine.proposals as proposals
        call_order = []

        def fake_run(cmd, **kw):
            joined = " ".join(str(c) for c in cmd)
            if "issue" in joined and "edit" in joined and "passed" in joined:
                call_order.append("close")
            elif "issue" in joined and "close" in joined:
                call_order.append("close")
            return ""

        def fake_world_md(*a, **kw):
            call_order.append("world_md")

        monkeypatch.setattr(proposals, "run", fake_run)
        monkeypatch.setattr(proposals, "gh_json", lambda *a, **kw: [])
        monkeypatch.setattr(proposals, "get_reactions", lambda n: (0, 0, [], []))
        monkeypatch.setattr(proposals, "generate_narrative", lambda *a, **kw: "narrative")
        monkeypatch.setattr(proposals, "generate_world_md", fake_world_md)
        monkeypatch.setattr(proposals, "update_readme", lambda *a, **kw: None)
        monkeypatch.setattr(proposals, "update_laws_index", lambda *a, **kw: None)
        monkeypatch.setattr(proposals, "update_proposal_cooldown", lambda *a, **kw: None)
        monkeypatch.setattr(proposals, "apply_effect", lambda *a, **kw: None)
        monkeypatch.setattr(proposals, "run_world_engine", lambda *a, **kw: [])
        monkeypatch.setattr(proposals, "check_proposal_cooldown", lambda *a, **kw: (True, "", 0))
        monkeypatch.setattr(proposals, "load_active_event", lambda: None)
        monkeypatch.setattr(proposals, "apply_crisis_multiplier", lambda e, ev: e)
        monkeypatch.setattr(proposals, "update_world_summary", lambda s: "")
        monkeypatch.setattr(proposals, "check_threshold_tags", lambda a, b: [])
        monkeypatch.setattr(proposals, "determine_era", lambda s: "Founding Era")
        monkeypatch.setattr(proposals, "read_state", lambda: {
            "laws_count": 12, "treasury": 1000, "currency": "GC", "era": "Founding Era",
        })
        monkeypatch.setattr(proposals, "write_state", lambda s: None)
        monkeypatch.setattr(proposals, "read_stats", lambda: {})
        monkeypatch.setattr(proposals, "write_stats", lambda s: None)
        monkeypatch.setattr(proposals, "SKIP_TIMING", True)

        law_dir = tmp_path / "world" / "laws"
        law_dir.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)

        issue = {
            "number": 99,
            "title": "[AI-PROPOSAL] Test",
            "body": "## Effect\n```yaml\ntype: policy\nchanges:\n  defense: +6\n```",
            "createdAt": "2020-01-01T00:00:00Z",
        }
        proposals.process_ai_proposal(issue)

        assert "close" in call_order, "Issue must be closed during processing"
        assert "world_md" in call_order, "generate_world_md must be called"
        close_idx = min(i for i, v in enumerate(call_order) if v == "close")
        wmd_idx = call_order.index("world_md")
        assert close_idx < wmd_idx, f"close (idx {close_idx}) must come before world_md (idx {wmd_idx})"


# ===========================================================================
# process_issue — cooldown block and treasury block paths
# ===========================================================================

class TestProcessIssueCooldownAndTreasury:
    def _make_world(self, tmp_path, treasury=200):
        (tmp_path / "world").mkdir(parents=True, exist_ok=True)
        (tmp_path / "world/laws").mkdir(parents=True, exist_ok=True)
        state = {**BASE_STATE, "treasury": treasury}
        (tmp_path / "world/state.json").write_text(json.dumps(state))
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

    def _policy_issue(self, age_hours=48):
        created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        body = (
            "## Effect\n\n```yaml\n"
            "type: policy\n"
            "changes:\n"
            "  education: 5\n"
            "```\n"
        )
        return {
            "number": 77,
            "title": "[PROPOSAL] Improve Education",
            "body": body,
            "createdAt": created,
            "author": {"login": "alice"},
        }

    def test_policy_proposal_blocked_by_cooldown(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": {"last_date": today, "streak": 1}}))
        issue = self._policy_issue()
        run_calls = []
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(3, 1, ["a", "b", "c"], ["d"])), \
             patch.object(_engine_proposals, "run",
                          side_effect=lambda cmd, **kw: run_calls.append(cmd) or ""):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_issue(issue)
            _engine_proposals.SKIP_TIMING = False
        closed = any("close" in cmd for cmd in run_calls)
        assert closed, "Cooldown-blocked proposal must be closed"
        stats = json.loads((tmp_path / "world/stats.json").read_text())
        assert stats.get("proposals_rejected", 0) >= 1

    def test_policy_proposal_blocked_by_insufficient_treasury(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path, treasury=0)
        issue = self._policy_issue()
        run_calls = []
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(3, 1, ["a", "b", "c"], ["d"])), \
             patch.object(_engine_proposals, "run",
                          side_effect=lambda cmd, **kw: run_calls.append(cmd) or ""):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_issue(issue)
            _engine_proposals.SKIP_TIMING = False
        closed = any("close" in cmd for cmd in run_calls)
        assert closed, "Treasury-blocked proposal must be closed"
        stats = json.loads((tmp_path / "world/stats.json").read_text())
        assert stats.get("proposals_rejected", 0) >= 1

    def test_policy_proposal_passes_with_sufficient_treasury(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path, treasury=500)
        issue = self._policy_issue()
        with patch.object(_engine_proposals, "get_reactions",
                          return_value=(3, 1, ["a", "b", "c"], ["d"])), \
             patch.object(_engine_proposals, "run", return_value=""), \
             patch.object(_engine_proposals, "generate_narrative", return_value="Narrative."), \
             patch.object(_engine_proposals, "generate_world_md", return_value=None), \
             patch.object(_engine_proposals, "update_readme", return_value=None), \
             patch.object(_engine_proposals, "append_history", return_value=None), \
             patch.object(_engine_proposals, "update_laws_index", return_value=None), \
             patch.object(_engine_proposals, "apply_tags", return_value=None), \
             patch.object(_engine_proposals, "run_world_engine", return_value=[]), \
             patch.object(_engine_proposals, "update_world_summary", return_value="summary"):
            _engine_proposals.SKIP_TIMING = True
            _engine_proposals.process_issue(issue)
            _engine_proposals.SKIP_TIMING = False
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["laws_count"] == BASE_STATE["laws_count"] + 1
