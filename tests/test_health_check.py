import json
import pytest
import sys
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from scripts import health_check

class TestHealthCheck:
    def _setup_world(self, tmp_path, next_tick_at=None, invalid_json=False):
        (tmp_path / "world").mkdir()
        state_file = tmp_path / "world/state.json"
        
        if invalid_json:
            state_file.write_text("{invalid")
            return
            
        state = {}
        if next_tick_at:
            state["next_tick_at"] = next_tick_at
        state_file.write_text(json.dumps(state))

    def test_missing_state_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc:
            health_check.main()
        assert exc.value.code == 0
        assert "No state.json found" in capsys.readouterr().out

    def test_invalid_json(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, invalid_json=True)
        with pytest.raises(SystemExit) as exc:
            health_check.main()
        assert exc.value.code == 0
        assert "Invalid state.json" in capsys.readouterr().out

    def test_missing_next_tick(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        with pytest.raises(SystemExit) as exc:
            health_check.main()
        assert exc.value.code == 0
        assert "No next_tick_at found" in capsys.readouterr().out

    def test_invalid_next_tick_format(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, next_tick_at="not-a-date")
        with pytest.raises(SystemExit) as exc:
            health_check.main()
        assert exc.value.code == 0
        assert "Invalid next_tick_at format" in capsys.readouterr().out

    @patch("scripts.health_check.gh_json")
    @patch("scripts.health_check.run")
    def test_healthy_world_no_issues(self, mock_run, mock_gh_json, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # 1 hour ago (healthy)
        next_tick = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._setup_world(tmp_path, next_tick_at=next_tick)
        
        mock_gh_json.return_value = []
        
        health_check.main()
        
        mock_gh_json.assert_called_once()
        mock_run.assert_not_called()
        assert "World is healthy" in capsys.readouterr().out

    @patch("scripts.health_check.gh_json")
    @patch("scripts.health_check.run")
    def test_healthy_world_closes_issues(self, mock_run, mock_gh_json, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # 1 hour ago (healthy)
        next_tick = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._setup_world(tmp_path, next_tick_at=next_tick)
        
        mock_gh_json.return_value = [
            {"number": 123, "title": "[HEALTH] World tick overdue by 7h"},
            {"number": 124, "title": "Other issue"} # Should be ignored because of title prefix check
        ]
        
        health_check.main()
        
        assert mock_run.call_count == 2 # 1 for comment, 1 for close
        
        # Verify comment call
        comment_call = mock_run.call_args_list[0][0][0]
        assert "comment" in comment_call
        assert "123" in comment_call
        
        # Verify close call
        close_call = mock_run.call_args_list[1][0][0]
        assert "close" in close_call
        assert "123" in close_call
        
        assert "Closing resolved health issue #123" in capsys.readouterr().out

    @patch("scripts.health_check.gh_json")
    @patch("scripts.health_check.run")
    def test_overdue_world_creates_issue(self, mock_run, mock_gh_json, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # 7 hours ago (overdue)
        next_tick = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._setup_world(tmp_path, next_tick_at=next_tick)
        
        mock_gh_json.return_value = []
        
        with pytest.raises(SystemExit) as exc:
            health_check.main()
            
        assert exc.value.code == 1
        assert mock_run.call_count == 1
        create_call = mock_run.call_args_list[0][0][0]
        assert "create" in create_call
        assert "--title" in create_call
        
        out = capsys.readouterr().out
        assert "ALERT: World is" in out
        assert "Created new health issue" in out

    @patch("scripts.health_check.gh_json")
    @patch("scripts.health_check.run")
    def test_overdue_world_deduplicates_issue(self, mock_run, mock_gh_json, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # 7 hours ago (overdue)
        next_tick = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._setup_world(tmp_path, next_tick_at=next_tick)
        
        mock_gh_json.return_value = [
            {"number": 999, "title": "[HEALTH] World tick overdue"}
        ]
        
        with pytest.raises(SystemExit) as exc:
            health_check.main()
            
        assert exc.value.code == 1
        mock_run.assert_not_called()
        
        out = capsys.readouterr().out
        assert "Health issue already exists (#999)" in out
