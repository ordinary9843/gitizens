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
