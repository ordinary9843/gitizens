import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import seed


# ---------------------------------------------------------------------------
# run()
# ---------------------------------------------------------------------------

class TestRun:
    def test_returns_stripped_stdout(self):
        result = MagicMock()
        result.stdout = "  output  "
        with patch("subprocess.run", return_value=result):
            out = seed.run(["echo", "output"])
        assert out == "output"

    def test_empty_stdout_returns_empty_string(self):
        result = MagicMock()
        result.stdout = ""
        with patch("subprocess.run", return_value=result):
            out = seed.run(["false"])
        assert out == ""


# ---------------------------------------------------------------------------
# create_issue()
# ---------------------------------------------------------------------------

class TestCreateIssue:
    def test_parses_issue_number_from_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        result = MagicMock()
        result.stdout = "https://github.com/org/repo/issues/7\n"
        with patch("subprocess.run", return_value=result):
            num = seed.create_issue("Test Title", "A description.", "type: policy")
        assert num == 7

    def test_writes_and_removes_body_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        seen_file = []
        def fake_run(cmd, **kwargs):
            for arg in cmd:
                if "_seed_body" in str(arg):
                    seen_file.append(Path(arg).read_text(encoding="utf-8"))
            r = MagicMock()
            r.stdout = "https://github.com/org/repo/issues/3\n"
            return r
        with patch("subprocess.run", side_effect=fake_run):
            seed.create_issue("T", "Desc.", "type: declaration")
        assert seen_file and "Desc." in seen_file[0]
        assert not (tmp_path / "scripts/_seed_body.txt").exists()

    def test_body_contains_effect_yaml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        captured = []
        def fake_run(cmd, **kwargs):
            for arg in cmd:
                if "_seed_body" in str(arg):
                    captured.append(Path(arg).read_text(encoding="utf-8"))
            r = MagicMock()
            r.stdout = "https://github.com/org/repo/issues/1\n"
            return r
        with patch("subprocess.run", side_effect=fake_run):
            seed.create_issue("X", "Desc.", "type: policy\nchanges:\n  education: +20")
        assert captured and "education: +20" in captured[0]


# ---------------------------------------------------------------------------
# add_reaction()
# ---------------------------------------------------------------------------

class TestAddReaction:
    def test_calls_gh_api(self):
        calls = []
        result = MagicMock()
        with patch("subprocess.run", side_effect=lambda cmd, **kw: calls.append(cmd) or result):
            seed.add_reaction(42)
        assert any("reactions" in str(c) for c in calls[0])
        assert any("42" in str(c) for c in calls[0])

    def test_uses_thumbs_up(self):
        calls = []
        result = MagicMock()
        with patch("subprocess.run", side_effect=lambda cmd, **kw: calls.append(cmd) or result):
            seed.add_reaction(1)
        cmd = calls[0]
        assert any("+1" in str(c) for c in cmd)


# ---------------------------------------------------------------------------
# add_label()
# ---------------------------------------------------------------------------

class TestAddLabel:
    def test_calls_gh_issue_edit(self):
        calls = []
        result = MagicMock()
        with patch("subprocess.run", side_effect=lambda cmd, **kw: calls.append(cmd) or result):
            seed.add_label(5)
        cmd = calls[0]
        assert "issue" in cmd
        assert "edit" in cmd
        assert "5" in cmd

    def test_adds_proposal_label(self):
        calls = []
        result = MagicMock()
        with patch("subprocess.run", side_effect=lambda cmd, **kw: calls.append(cmd) or result):
            seed.add_label(10)
        cmd = calls[0]
        assert "proposal" in cmd


# ---------------------------------------------------------------------------
# run_tally()
# ---------------------------------------------------------------------------

class TestRunTally:
    def test_returns_true_on_zero_exit_code(self):
        result = MagicMock()
        result.returncode = 0
        auth_result = MagicMock()
        auth_result.stdout = "ghp_token\n"

        def fake_run(cmd, **kwargs):
            if "auth" in cmd:
                return auth_result
            return result

        with patch("subprocess.run", side_effect=fake_run):
            assert seed.run_tally() is True

    def test_returns_false_on_nonzero_exit_code(self):
        result = MagicMock()
        result.returncode = 1
        auth_result = MagicMock()
        auth_result.stdout = "ghp_token\n"

        def fake_run(cmd, **kwargs):
            if "auth" in cmd:
                return auth_result
            return result

        with patch("subprocess.run", side_effect=fake_run):
            assert seed.run_tally() is False

    def test_sets_skip_timing_env(self):
        envs = []
        result = MagicMock()
        result.returncode = 0
        auth_result = MagicMock()
        auth_result.stdout = "ghp_token\n"

        def fake_run(cmd, **kwargs):
            if "auth" in cmd:
                return auth_result
            envs.append(kwargs.get("env", {}))
            return result

        with patch("subprocess.run", side_effect=fake_run):
            seed.run_tally()
        assert envs and envs[0].get("SKIP_TIMING_CHECK") == "1"
