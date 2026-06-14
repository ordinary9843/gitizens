import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

# helpers.py sets GITHUB_TOKEN / GITHUB_REPOSITORY and stubs openai before any engine import
from tests.helpers import tv  # noqa: F401 — side-effect: env setup

import auto_propose


# ---------------------------------------------------------------------------
# _load_extra_context
# ---------------------------------------------------------------------------

class TestLoadExtraContext:
    def test_all_sources_succeed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/entities/buildings").mkdir(parents=True)
        (tmp_path / "world/entities/buildings/bld-001.json").write_text(
            json.dumps({"name": "Library"}))
        (tmp_path / "world/entities/buildings/_index.json").write_text(
            json.dumps({"entities": ["bld-001"]}))
        (tmp_path / "world").mkdir(exist_ok=True)
        laws = [{"title": "Law A"}, {"title": "Law B"}, {"title": "Law C"}, {"title": "Law D"}]
        (tmp_path / "world/laws_index.json").write_text(json.dumps(laws))
        fake_event = {"title": "Great Flood"}
        monkeypatch.setattr(auto_propose, "load_active_event", lambda: fake_event)

        bld, laws_ctx, evt = auto_propose._load_extra_context()
        assert "library" in bld.lower()
        assert "Law B" in laws_ctx and "Law C" in laws_ctx and "Law D" in laws_ctx
        assert "Law A" not in laws_ctx  # only last 3
        assert "Great Flood" in evt

    def test_entity_exception_returns_empty_buildings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_entity_names",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(auto_propose, "load_active_event", lambda: None)
        bld, _, _ = auto_propose._load_extra_context()
        assert bld == ""

    def test_laws_exception_returns_empty_laws(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_entity_names", lambda: set())
        monkeypatch.setattr(auto_propose, "load_active_event", lambda: None)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("CORRUPT")
        _, laws_ctx, _ = auto_propose._load_extra_context()
        assert laws_ctx == ""

    def test_no_laws_file_gives_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_entity_names", lambda: set())
        monkeypatch.setattr(auto_propose, "load_active_event", lambda: None)
        (tmp_path / "world").mkdir()
        _, laws_ctx, _ = auto_propose._load_extra_context()
        assert laws_ctx == ""

    def test_event_exception_returns_empty_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_entity_names", lambda: set())
        monkeypatch.setattr(auto_propose, "load_active_event",
                            lambda: (_ for _ in ()).throw(RuntimeError("oops")))
        (tmp_path / "world").mkdir()
        _, _, evt = auto_propose._load_extra_context()
        assert evt == ""

    def test_active_event_no_title_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_entity_names", lambda: set())
        monkeypatch.setattr(auto_propose, "load_active_event", lambda: {"id": "e1"})
        (tmp_path / "world").mkdir()
        _, _, evt = auto_propose._load_extra_context()
        assert evt == ""


# ---------------------------------------------------------------------------
# _run
# ---------------------------------------------------------------------------

class TestRun:
    def test_returns_stripped_stdout(self):
        result = MagicMock()
        result.returncode = 0
        result.stdout = "  hello  "
        result.stderr = ""
        with patch("subprocess.run", return_value=result):
            out = auto_propose._run(["echo", "hello"])
        assert out == "hello"

    def test_warns_on_stderr(self, capsys):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "error message"
        with patch("subprocess.run", return_value=result):
            auto_propose._run(["gh", "fail"])
        captured = capsys.readouterr()
        assert "WARN" in captured.out

    def test_single_arg_no_index_error(self, capsys):
        result = MagicMock()
        result.returncode = 1
        result.stdout = ""
        result.stderr = "oops"
        with patch("subprocess.run", return_value=result):
            auto_propose._run(["onetool"])
        captured = capsys.readouterr()
        assert "WARN" in captured.out


# ---------------------------------------------------------------------------
# _gh_json
# ---------------------------------------------------------------------------

class TestGhJson:
    def test_parses_json_output(self):
        with patch.object(auto_propose, "_run", return_value='[{"number": 1}]'):
            result = auto_propose._gh_json(["issue", "list"])
        assert result == [{"number": 1}]

    def test_empty_output_returns_empty_list(self):
        with patch.object(auto_propose, "_run", return_value=""):
            result = auto_propose._gh_json(["issue", "list"])
        assert result == []


# ---------------------------------------------------------------------------
# _open_count
# ---------------------------------------------------------------------------

class TestOpenCount:
    def test_counts_items(self):
        items = [{"number": 1}, {"number": 2}]
        with patch.object(auto_propose, "_gh_json", return_value=items):
            assert auto_propose._open_count("test/repo", "ai-proposal") == 2

    def test_empty_returns_zero(self):
        with patch.object(auto_propose, "_gh_json", return_value=[]):
            assert auto_propose._open_count("test/repo", "feedback") == 0


# ---------------------------------------------------------------------------
# should_generate
# ---------------------------------------------------------------------------

class TestShouldGenerate:
    def test_no_proposals_no_feedback(self):
        with patch.object(auto_propose, "_open_count", side_effect=[0, 0]):
            proposal, feedback = auto_propose.should_generate("test/repo")
        assert proposal is True
        assert feedback is True

    def test_existing_proposal_blocks_generation(self):
        with patch.object(auto_propose, "_open_count", side_effect=[1, 0]):
            proposal, feedback = auto_propose.should_generate("test/repo")
        assert proposal is False
        assert feedback is True

    def test_two_feedbacks_blocks_feedback(self):
        with patch.object(auto_propose, "_open_count", side_effect=[0, 2]):
            proposal, feedback = auto_propose.should_generate("test/repo")
        assert proposal is True
        assert feedback is False


# ---------------------------------------------------------------------------
# _post_issue
# ---------------------------------------------------------------------------

class TestPostIssue:
    def test_parses_issue_number_from_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        with patch.object(auto_propose, "_run",
                          return_value="https://github.com/test/repo/issues/42"):
            num = auto_propose._post_issue("test/repo", "[AI] Title", "body", "ai-proposal")
        assert num == 42

    def test_invalid_url_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        with patch.object(auto_propose, "_run", return_value="not-a-url"):
            num = auto_propose._post_issue("test/repo", "T", "B", "lbl")
        assert num == 0

    def test_writes_and_cleans_up_body_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        written = []
        def fake_run(cmd):
            body_file = [a for a in cmd if "_ai_body" in str(a)]
            if body_file:
                written.append(Path(body_file[0]).read_text(encoding="utf-8"))
            return "https://github.com/test/repo/issues/5"
        with patch.object(auto_propose, "_run", side_effect=fake_run):
            auto_propose._post_issue("test/repo", "T", "my body content", "lbl")
        assert written and "my body content" in written[0]
        assert not (tmp_path / "scripts/_ai_body.txt").exists()


# ---------------------------------------------------------------------------
# generate_ai_proposal
# ---------------------------------------------------------------------------

class TestGenerateAiProposal:
    def _state(self):
        return {"education": 30, "industry": 50, "welfare": 50,
                "green_policy": 50, "defense": 50,
                "era": "Iron Age", "treasury": 100, "population": 500,
                "stability": 80}

    def test_llm_success_posts_issue(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        client = MagicMock()
        client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
            {"title": "Boost Schools", "description": "Invest in education.", "delta": 7})
        with patch.object(auto_propose, "_post_issue", return_value=10) as mock_post:
            num = auto_propose.generate_ai_proposal(client, self._state(), "test/repo")
        assert num == 10
        title_arg = mock_post.call_args[0][1]
        assert "[AI-PROPOSAL]" in title_arg
        assert "Boost Schools" in title_arg

    def test_llm_exception_uses_fallback(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("API down")
        with patch.object(auto_propose, "_post_issue", return_value=11) as mock_post:
            num = auto_propose.generate_ai_proposal(client, self._state(), "test/repo")
        assert num == 11
        # Fallback targets the weakest metric (education=30)
        body_arg = mock_post.call_args[0][2]
        assert "education" in body_arg

    def test_delta_clamped_to_max(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        client = MagicMock()
        client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
            {"title": "Huge Boost", "description": "Big changes.", "delta": 999})
        with patch.object(auto_propose, "_post_issue", return_value=1) as mock_post:
            auto_propose.generate_ai_proposal(client, self._state(), "test/repo")
        body_arg = mock_post.call_args[0][2]
        assert f"+{auto_propose.MAX_DELTA_PROPOSAL}" in body_arg

    def test_delta_floored_to_three(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        client = MagicMock()
        client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
            {"title": "Tiny", "description": "Small.", "delta": 1})
        with patch.object(auto_propose, "_post_issue", return_value=1) as mock_post:
            auto_propose.generate_ai_proposal(client, self._state(), "test/repo")
        body_arg = mock_post.call_args[0][2]
        assert "+3" in body_arg


# ---------------------------------------------------------------------------
# generate_feedbacks
# ---------------------------------------------------------------------------

class TestGenerateFeedbacks:
    def _state(self):
        return {"education": 50, "industry": 50, "welfare": 50,
                "green_policy": 50, "defense": 50,
                "era": "Modern", "population": 1000,
                "pollution": 20, "stability": 80}

    def _client_with(self, feedbacks: list):
        client = MagicMock()
        client.chat.completions.create.return_value.choices[0].message.content = json.dumps(
            {"feedbacks": feedbacks})
        return client

    def test_posts_valid_feedbacks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        fbs = [
            {"title": "Noise", "description": "Loud.", "metric": "welfare", "delta": -1},
            {"title": "Park", "description": "Nice park.", "metric": "green_policy", "delta": 2},
        ]
        client = self._client_with(fbs)
        with patch.object(auto_propose, "_post_issue", side_effect=[20, 21]):
            nums = auto_propose.generate_feedbacks(client, self._state(), "test/repo", count=2)
        assert nums == [20, 21]

    def test_invalid_metric_defaults_to_welfare(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        fbs = [{"title": "X", "description": "Y.", "metric": "BOGUS", "delta": 1}]
        client = self._client_with(fbs)
        with patch.object(auto_propose, "_post_issue", return_value=5) as mock_post:
            auto_propose.generate_feedbacks(client, self._state(), "test/repo", count=1)
        body_arg = mock_post.call_args[0][2]
        assert "welfare" in body_arg

    def test_zero_delta_becomes_one(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        fbs = [{"title": "X", "description": "Y.", "metric": "welfare", "delta": 0}]
        client = self._client_with(fbs)
        with patch.object(auto_propose, "_post_issue", return_value=6) as mock_post:
            auto_propose.generate_feedbacks(client, self._state(), "test/repo", count=1)
        body_arg = mock_post.call_args[0][2]
        assert "+1" in body_arg

    def test_delta_clamped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        fbs = [{"title": "X", "description": "Y.", "metric": "defense", "delta": 99}]
        client = self._client_with(fbs)
        with patch.object(auto_propose, "_post_issue", return_value=7) as mock_post:
            auto_propose.generate_feedbacks(client, self._state(), "test/repo", count=1)
        body_arg = mock_post.call_args[0][2]
        assert f"+{auto_propose.MAX_DELTA_FEEDBACK}" in body_arg

    def test_valueerror_in_fb_skips(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        fbs = [{"title": "X", "description": "Y.", "metric": "welfare", "delta": "bad"}]
        client = self._client_with(fbs)
        with patch.object(auto_propose, "_post_issue", return_value=0) as mock_post:
            nums = auto_propose.generate_feedbacks(client, self._state(), "test/repo", count=1)
        mock_post.assert_not_called()
        assert nums == []

    def test_llm_exception_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("network down")
        with patch.object(auto_propose, "_post_issue", return_value=0) as mock_post:
            nums = auto_propose.generate_feedbacks(client, self._state(), "test/repo")
        mock_post.assert_not_called()
        assert nums == []

    def test_issue_number_zero_not_appended(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        fbs = [{"title": "X", "description": "Y.", "metric": "welfare", "delta": 1}]
        client = self._client_with(fbs)
        with patch.object(auto_propose, "_post_issue", return_value=0):
            nums = auto_propose.generate_feedbacks(client, self._state(), "test/repo", count=1)
        assert nums == []

    def test_stability_and_pollution_are_valid_metrics(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(auto_propose, "_load_extra_context",
                            lambda: ("", "", ""))
        for metric in ("stability", "pollution"):
            fbs = [{"title": "X", "description": "Y.", "metric": metric, "delta": 1}]
            client = self._client_with(fbs)
            with patch.object(auto_propose, "_post_issue", return_value=1) as mock_post:
                auto_propose.generate_feedbacks(client, self._state(), "test/repo", count=1)
            body_arg = mock_post.call_args[0][2]
            assert metric in body_arg
