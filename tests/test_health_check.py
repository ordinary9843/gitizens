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
        mock_run.assert_called_once()
        create_call = mock_run.call_args[0][0]
        assert "issue" in create_call
        assert "create" in create_call
        assert "--title" in create_call
        assert "[HEALTH] World tick overdue by 7h" in create_call
        assert "Created new health issue:" in capsys.readouterr().out

    @patch("scripts.health_check.gh_json")
    @patch("scripts.health_check.run")
    def test_overdue_world_issue_exists(self, mock_run, mock_gh_json, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # 7 hours ago (overdue)
        next_tick = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._setup_world(tmp_path, next_tick_at=next_tick)
        
        mock_gh_json.return_value = [{"number": 123, "title": "[HEALTH] World tick overdue by 5h"}]
        
        with pytest.raises(SystemExit) as exc:
            health_check.main()
        
        assert exc.value.code == 1
        mock_run.assert_not_called()
        assert "Health issue already exists" in capsys.readouterr().out

    def test_run_success(self):
        result = health_check.run(["python", "-c", "print('hello')"])
        assert "hello" in result

    def test_run_failure(self, capsys):
        result = health_check.run(["python", "-c", "import sys; sys.stderr.write('error'); sys.exit(1)"])
        assert result == ""
        assert "[WARN] python -c:" in capsys.readouterr().out

    def test_run_exception(self, capsys):
        with patch("subprocess.run", side_effect=Exception("mocked error")):
            result = health_check.run(["gh", "issue"])
            assert result == ""
            assert "[WARN] run(['gh', 'issue']): mocked error" in capsys.readouterr().out

    def test_gh_json_success(self):
        with patch("scripts.health_check.run", return_value='[{"id": 1}]'):
            assert health_check.gh_json(["gh", "issue"]) == [{"id": 1}]

    def test_gh_json_failure(self):
        with patch("scripts.health_check.run", return_value='invalid json'):
            assert health_check.gh_json(["gh", "issue"]) == []

    @patch("scripts.health_check.gh_json")
    @patch("scripts.health_check.run")
    def test_healthy_world_closes_issues_error(self, mock_run, mock_gh_json, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        next_tick = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._setup_world(tmp_path, next_tick_at=next_tick)
        
        mock_gh_json.return_value = [{"number": 123, "title": "[HEALTH] error"}]
        mock_run.side_effect = Exception("failed to close")
        
        health_check.main()
        assert "Could not close health issue #123: failed to close" in capsys.readouterr().out

    def test_naive_datetime(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # Without Z
        next_tick = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        self._setup_world(tmp_path, next_tick_at=next_tick)
        with patch("scripts.health_check.gh_json", return_value=[]):
            health_check.main()
        assert "World is healthy" in capsys.readouterr().out

    @patch("scripts.health_check.gh_json")
    def test_gh_json_exception(self, mock_gh_json, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        next_tick = (datetime.now(timezone.utc) - timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._setup_world(tmp_path, next_tick_at=next_tick)
        
        mock_gh_json.side_effect = Exception("API Error")
        
        with patch("scripts.health_check.run") as mock_run:
            with pytest.raises(SystemExit) as exc:
                health_check.main()
            assert exc.value.code == 1
            assert mock_run.call_count == 1
        
        assert "Failed to query issues: API Error" in capsys.readouterr().out

