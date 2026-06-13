"""
Tests for scripts/engine/gh.py — run, gh_json, push_with_retry.
"""
import json
import os
import sys
from unittest.mock import patch, MagicMock, call

import pytest

# helpers.py imports scripts.tally_votes which adds scripts/ to sys.path,
# making the engine package importable.  Import it first.
from tests.helpers import _engine_gh  # noqa: F401 — side-effect: sys.path setup

import engine.gh as gh


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_completed_process(returncode=0, stdout="", stderr=""):
    mock = MagicMock()
    mock.returncode = returncode
    mock.stdout = stdout
    mock.stderr = stderr
    return mock


# ===========================================================================
# run(cmd)
# ===========================================================================

class TestRun:
    def test_calls_subprocess_with_correct_args(self):
        proc = _make_completed_process(stdout="output\n")
        with patch("engine.gh.subprocess.run", return_value=proc) as mock_sub:
            gh.run(["git", "status"])
        mock_sub.assert_called_once_with(
            ["git", "status"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
        )

    def test_returns_stdout_stripped(self):
        proc = _make_completed_process(stdout="  hello world  \n")
        with patch("engine.gh.subprocess.run", return_value=proc):
            result = gh.run(["git", "status"])
        assert result == "hello world"

    def test_returns_empty_string_when_stdout_is_empty(self):
        proc = _make_completed_process(stdout="")
        with patch("engine.gh.subprocess.run", return_value=proc):
            result = gh.run(["git", "log"])
        assert result == ""

    def test_nonzero_exit_still_returns_stdout(self):
        # run() does NOT raise; it prints a warning and returns stdout stripped.
        proc = _make_completed_process(returncode=1, stdout="partial output", stderr="some error")
        with patch("engine.gh.subprocess.run", return_value=proc):
            result = gh.run(["git", "push"])
        assert result == "partial output"

    def test_nonzero_exit_with_empty_stderr_no_warn(self):
        # When stderr is empty, no warning is printed but stdout is still returned.
        proc = _make_completed_process(returncode=1, stdout="out", stderr="")
        with patch("engine.gh.subprocess.run", return_value=proc):
            result = gh.run(["cmd"])
        assert result == "out"

    def test_zero_exit_returns_stdout(self):
        proc = _make_completed_process(returncode=0, stdout="success\n")
        with patch("engine.gh.subprocess.run", return_value=proc):
            result = gh.run(["gh", "api", "something"])
        assert result == "success"


# ===========================================================================
# gh_json(cmd)
# ===========================================================================

class TestGhJson:
    def test_prepends_gh_to_cmd(self):
        proc = _make_completed_process(stdout='[]')
        with patch("engine.gh.subprocess.run", return_value=proc) as mock_sub:
            gh.gh_json(["issue", "list"])
        actual_cmd = mock_sub.call_args[0][0]
        assert actual_cmd[0] == "gh"
        assert actual_cmd[1:] == ["issue", "list"]

    def test_parses_json_list(self):
        data = [{"id": 1, "title": "Test"}]
        proc = _make_completed_process(stdout=json.dumps(data))
        with patch("engine.gh.subprocess.run", return_value=proc):
            result = gh.gh_json(["issue", "list"])
        assert result == data

    def test_parses_json_dict(self):
        data = {"number": 42, "state": "open"}
        proc = _make_completed_process(stdout=json.dumps(data))
        with patch("engine.gh.subprocess.run", return_value=proc):
            result = gh.gh_json(["issue", "view", "42"])
        assert result == data

    def test_returns_empty_list_on_empty_output(self):
        proc = _make_completed_process(stdout="")
        with patch("engine.gh.subprocess.run", return_value=proc):
            result = gh.gh_json(["issue", "list"])
        assert result == []

    def test_raises_on_invalid_json(self):
        # gh_json does not suppress JSONDecodeError; invalid output propagates.
        proc = _make_completed_process(stdout="not valid json {{{")
        with patch("engine.gh.subprocess.run", return_value=proc):
            with pytest.raises(json.JSONDecodeError):
                gh.gh_json(["issue", "list"])

    def test_parses_nested_json(self):
        data = [{"number": 1, "reactions": {"+1": 5, "-1": 2}}]
        proc = _make_completed_process(stdout=json.dumps(data))
        with patch("engine.gh.subprocess.run", return_value=proc):
            result = gh.gh_json(["issue", "list", "--json", "number,reactions"])
        assert result[0]["reactions"]["+1"] == 5


# ===========================================================================
# push_with_retry(max_attempts)
# ===========================================================================

class TestPushWithRetry:
    def _make_push_result(self, returncode=0, stderr=""):
        mock = MagicMock()
        mock.returncode = returncode
        mock.stderr = stderr
        return mock

    def test_succeeds_on_first_attempt(self):
        pull_proc = _make_completed_process(stdout="")
        push_proc = self._make_push_result(returncode=0)
        with patch("engine.gh.subprocess.run") as mock_sub:
            # First call is git pull (via run()), second is git push (direct subprocess.run)
            mock_sub.side_effect = [pull_proc, push_proc]
            result = gh.push_with_retry(max_attempts=3)
        assert result is True

    def test_returns_false_after_all_retries_exhausted(self):
        pull_proc = _make_completed_process(stdout="")
        push_fail = self._make_push_result(returncode=1, stderr="rejected")
        # Each attempt: one pull + one push
        with patch("engine.gh.subprocess.run") as mock_sub, \
             patch("engine.gh.time.sleep"):
            mock_sub.side_effect = [
                pull_proc, push_fail,   # attempt 1
                pull_proc, push_fail,   # attempt 2
                pull_proc, push_fail,   # attempt 3
            ]
            result = gh.push_with_retry(max_attempts=3)
        assert result is False

    def test_returns_true_on_second_attempt(self):
        pull_proc = _make_completed_process(stdout="")
        push_fail = self._make_push_result(returncode=1, stderr="conflict")
        push_ok = self._make_push_result(returncode=0)
        with patch("engine.gh.subprocess.run") as mock_sub, \
             patch("engine.gh.time.sleep"):
            mock_sub.side_effect = [
                pull_proc, push_fail,   # attempt 1 fails
                pull_proc, push_ok,     # attempt 2 succeeds
            ]
            result = gh.push_with_retry(max_attempts=3)
        assert result is True

    def test_no_sleep_on_first_attempt(self):
        pull_proc = _make_completed_process(stdout="")
        push_ok = self._make_push_result(returncode=0)
        with patch("engine.gh.subprocess.run") as mock_sub, \
             patch("engine.gh.time.sleep") as mock_sleep:
            mock_sub.side_effect = [pull_proc, push_ok]
            gh.push_with_retry(max_attempts=3)
        mock_sleep.assert_not_called()

    def test_sleep_called_on_retry(self):
        pull_proc = _make_completed_process(stdout="")
        push_fail = self._make_push_result(returncode=1, stderr="fail")
        push_ok = self._make_push_result(returncode=0)
        with patch("engine.gh.subprocess.run") as mock_sub, \
             patch("engine.gh.time.sleep") as mock_sleep:
            mock_sub.side_effect = [
                pull_proc, push_fail,
                pull_proc, push_ok,
            ]
            gh.push_with_retry(max_attempts=3)
        mock_sleep.assert_called_once_with(5)  # 5 * 1 on second iteration (i=1)

    def test_max_attempts_one_no_retry(self):
        pull_proc = _make_completed_process(stdout="")
        push_fail = self._make_push_result(returncode=1, stderr="fail")
        with patch("engine.gh.subprocess.run") as mock_sub, \
             patch("engine.gh.time.sleep"):
            mock_sub.side_effect = [pull_proc, push_fail]
            result = gh.push_with_retry(max_attempts=1)
        assert result is False

    def test_push_command_includes_follow_tags(self):
        pull_proc = _make_completed_process(stdout="")
        push_ok = self._make_push_result(returncode=0)
        with patch("engine.gh.subprocess.run") as mock_sub:
            mock_sub.side_effect = [pull_proc, push_ok]
            gh.push_with_retry(max_attempts=1)
        # The second call is the push; verify it contains --follow-tags
        push_call_args = mock_sub.call_args_list[1][0][0]
        assert "--follow-tags" in push_call_args
        assert "origin" in push_call_args
        assert "master" in push_call_args
