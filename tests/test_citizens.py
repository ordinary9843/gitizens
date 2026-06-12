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
