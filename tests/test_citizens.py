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
# Achievement system
# ===========================================================================

class TestAchievements:
    def test_first_vote_awarded(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        result = tv.track_citizen_activity(["alice"], [])
        assert "alice" in result
        assert "first_vote" in result["alice"]

    def test_civic_duty_awarded_at_threshold(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {"alice": {"total_votes": 9, "total_proposals": 0,
                              "last_active": "2026-06-01T00:00:00+00:00",
                              "achievements": ["first_vote"]}}
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        result = tv.track_citizen_activity(["alice"], [])
        assert "civic_duty" in result.get("alice", [])
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "civic_duty" in data["alice"]["achievements"]

    def test_no_duplicate_awards(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {"alice": {"total_votes": 5, "total_proposals": 0,
                              "last_active": "2026-06-01T00:00:00+00:00",
                              "achievements": ["first_vote"]}}
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        result = tv.track_citizen_activity(["alice"], [])
        assert result.get("alice", []) == []

    def test_legislator_awarded_on_proposal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        awarded = tv.track_citizen_proposal("alice")
        assert "legislator" in awarded
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "legislator" in data["alice"]["achievements"]

    def test_achievements_persist_in_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        tv.track_citizen_activity(["alice"], [])
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "achievements" in data["alice"]
        assert isinstance(data["alice"]["achievements"], list)
        assert "first_vote" in data["alice"]["achievements"]

    def test_representative_achievement_on_election(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "alice": {"total_votes": 50, "total_proposals": 0,
                      "last_active": "2026-06-01T00:00:00+00:00", "achievements": []},
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        (tmp_path / "world/representatives.json").write_text(json.dumps({"selected_at": None}))
        with patch.object(tv, "get_or_create_dispatch_issue", return_value=13), \
             patch.object(tv, "run", return_value=""):
            tv.select_weekly_representatives()
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "representative" in data["alice"]["achievements"]


# ===========================================================================
# _streak_penalty — direct unit tests
# ===========================================================================

class TestStreakPenalty:
    def _penalty(self, streak):
        import scripts.engine.citizens as _cit
        return _cit._streak_penalty(streak)

    def test_streak_0_returns_zero(self):
        assert self._penalty(0) == 0

    def test_streak_1_returns_zero(self):
        assert self._penalty(1) == 0

    def test_streak_2_returns_base(self):
        assert self._penalty(2) == 100

    def test_streak_3_doubles(self):
        assert self._penalty(3) == 200

    def test_streak_4_doubles_again(self):
        assert self._penalty(4) == 400

    def test_streak_5_doubles_again(self):
        assert self._penalty(5) == 800

    def test_monotonically_increasing(self):
        penalties = [self._penalty(s) for s in range(1, 8)]
        assert penalties == sorted(penalties)


# ===========================================================================
# Higher-tier achievements — active_citizen, veteran_legislator
# ===========================================================================

class TestHigherTierAchievements:

    def test_active_citizen_awarded_at_25_votes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "alice": {
                "total_votes": 24,
                "total_proposals": 0,
                "last_active": "2026-06-01T00:00:00+00:00",
                "achievements": ["first_vote", "civic_duty"],
            }
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        tv.track_citizen_activity(["alice"], [])
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "active_citizen" in data["alice"]["achievements"]

    def test_active_citizen_not_awarded_at_24_votes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "alice": {
                "total_votes": 23,
                "total_proposals": 0,
                "last_active": "2026-06-01T00:00:00+00:00",
                "achievements": ["first_vote", "civic_duty"],
            }
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        tv.track_citizen_activity(["alice"], [])
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "active_citizen" not in data["alice"]["achievements"]

    def test_veteran_legislator_awarded_at_5_proposals(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "bob": {
                "total_votes": 0,
                "total_proposals": 4,
                "last_active": "2026-06-01T00:00:00+00:00",
                "achievements": ["legislator"],
            }
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        tv.track_citizen_proposal("bob")
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "veteran_legislator" in data["bob"]["achievements"]

    def test_veteran_legislator_not_awarded_at_4_proposals(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "bob": {
                "total_votes": 0,
                "total_proposals": 3,
                "last_active": "2026-06-01T00:00:00+00:00",
                "achievements": ["legislator"],
            }
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        tv.track_citizen_proposal("bob")
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert "veteran_legislator" not in data["bob"]["achievements"]

    def test_no_duplicate_active_citizen(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "alice": {
                "total_votes": 25,
                "total_proposals": 0,
                "last_active": "2026-06-01T00:00:00+00:00",
                "achievements": ["first_vote", "civic_duty", "active_citizen"],
            }
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        tv.track_citizen_activity(["alice"], [])
        data = json.loads((tmp_path / "world/citizens.json").read_text())
        assert data["alice"]["achievements"].count("active_citizen") == 1


# ===========================================================================
# select_weekly_representatives — edge cases
# ===========================================================================

class TestSelectRepresentativesEdgeCases:
    def test_corrupted_citizens_json_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/citizens.json").write_text("{not valid json")
        original_reps = json.dumps({"selected_at": None, "representatives": ["old"]})
        (tmp_path / "world/representatives.json").write_text(original_reps)
        tv.select_weekly_representatives()
        reps = json.loads((tmp_path / "world/representatives.json").read_text())
        assert reps["representatives"] == ["old"]

    def test_malformed_selected_at_proceeds_with_selection(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "alice": {"total_votes": 10, "total_proposals": 0, "last_active": "", "achievements": []},
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        (tmp_path / "world/representatives.json").write_text(
            json.dumps({"selected_at": "not-a-date", "representatives": []}))
        with patch.object(tv, "get_or_create_dispatch_issue", return_value=13), \
             patch.object(tv, "run", return_value=""):
            tv.select_weekly_representatives()
        reps = json.loads((tmp_path / "world/representatives.json").read_text())
        assert "alice" in reps["representatives"]

    def test_no_reps_file_creates_it(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "alice": {"total_votes": 5, "total_proposals": 0, "last_active": "", "achievements": []},
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        with patch.object(tv, "get_or_create_dispatch_issue", return_value=13), \
             patch.object(tv, "run", return_value=""):
            tv.select_weekly_representatives()
        assert (tmp_path / "world/representatives.json").exists()
        reps = json.loads((tmp_path / "world/representatives.json").read_text())
        assert "alice" in reps["representatives"]


# ===========================================================================
# update_proposal_cooldown — edge cases
# ===========================================================================

class TestUpdateProposalCooldownEdgeCases:
    def test_bad_last_date_resets_streak_to_one(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        cooldowns = {"education": {"last_date": "NOT-A-DATE", "streak": 3}}
        (tmp_path / "world/proposal_cooldowns.json").write_text(json.dumps(cooldowns))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        effect_data = {"type": "policy", "changes": {"education": 10}}
        tv.update_proposal_cooldown(effect_data, today)
        data = json.loads((tmp_path / "world/proposal_cooldowns.json").read_text())
        assert data["education"]["streak"] == 1

    def test_missing_last_date_resets_to_one(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        cooldowns = {"education": {"last_date": None, "streak": 2}}
        (tmp_path / "world/proposal_cooldowns.json").write_text(json.dumps(cooldowns))
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        effect_data = {"type": "policy", "changes": {"education": 10}}
        tv.update_proposal_cooldown(effect_data, today)
        data = json.loads((tmp_path / "world/proposal_cooldowns.json").read_text())
        assert data["education"]["streak"] == 1


# ===========================================================================
# select_weekly_representatives — more edge cases
# ===========================================================================

class TestSelectRepresentativesMoreEdges:
    def test_no_citizens_file_returns_early(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/representatives.json").write_text(
            json.dumps({"selected_at": None, "representatives": ["old"]}))
        tv.select_weekly_representatives()
        reps = json.loads((tmp_path / "world/representatives.json").read_text())
        assert reps["representatives"] == ["old"]

    def test_empty_citizens_returns_early(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/citizens.json").write_text("{}")
        (tmp_path / "world/representatives.json").write_text(
            json.dumps({"selected_at": None, "representatives": ["old"]}))
        tv.select_weekly_representatives()
        reps = json.loads((tmp_path / "world/representatives.json").read_text())
        assert reps["representatives"] == ["old"]

    def test_award_achievement_exception_handled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        citizens = {
            "alice": {"total_votes": 10, "total_proposals": 0, "last_active": "",
                      "achievements": []},
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        (tmp_path / "world/representatives.json").write_text(
            json.dumps({"selected_at": None}))
        with patch.object(tv, "get_or_create_dispatch_issue", return_value=1),              patch.object(tv, "run", return_value=""),              patch("engine.citizens._award_achievements",
                   side_effect=RuntimeError("boom")):
            tv.select_weekly_representatives()
