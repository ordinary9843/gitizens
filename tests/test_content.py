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
# generate_annals
# ===========================================================================

class TestAnnalsGeneration:
    def test_no_generation_before_interval(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/annals").mkdir(parents=True)
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        history = [{"tick": i + 1} for i in range(9)]  # ticks 1-9, last is 9 → 9%10≠0
        tv.generate_annals(history)
        assert not list((tmp_path / "world/annals").glob("*.md"))

    def test_no_generation_on_empty_history(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/annals").mkdir(parents=True)
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        tv.generate_annals([])
        assert not list((tmp_path / "world/annals").glob("*.md"))

    def test_generation_at_interval(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/annals").mkdir(parents=True)
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        history = [{"tick": i + 1, "laws_count": 0, "population": 1000,
                    "treasury": 0, "era": "Founding Era"} for i in range(10)]  # ticks 1-10
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "# World Annals — Chapter 1\n\nTest content."
        )
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "run", return_value=""):
            tv.generate_annals(history)
        assert (tmp_path / "world/annals/chapter-001.md").exists()

    def test_no_duplicate_generation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/annals").mkdir(parents=True)
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        chapter = tmp_path / "world/annals/chapter-001.md"
        chapter.write_text("existing content\n")
        history = [{"tick": i + 1, "laws_count": 0, "population": 1000,
                    "treasury": 0} for i in range(10)]  # ticks 1-10 → chapter-001 already exists
        mock_client = MagicMock()
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "run", return_value=""):
            tv.generate_annals(history)
        mock_client.chat.completions.create.assert_not_called()
        assert chapter.read_text() == "existing content\n"


# ===========================================================================
# _state_for_llm — strips large fields before LLM prompts
# ===========================================================================

class TestStateForLlm:
    def test_known_stargazers_stripped(self):
        state = {**BASE_STATE, "known_stargazers": ["alice", "bob", "carol"],
                 "tags_applied": ["era/founding-era"]}
        result = tv._state_for_llm(state)
        assert "known_stargazers" not in result
        assert "tags_applied" not in result

    def test_policy_metrics_preserved(self):
        state = {**BASE_STATE, "known_stargazers": ["x"]}
        result = tv._state_for_llm(state)
        assert result["education"] == BASE_STATE["education"]
        assert result["treasury"] == BASE_STATE["treasury"]

    def test_empty_state_no_crash(self):
        result = tv._state_for_llm({})
        assert result == {}

    def test_original_not_mutated(self):
        state = {**BASE_STATE, "known_stargazers": ["alice"]}
        tv._state_for_llm(state)
        assert "known_stargazers" in state  # original unchanged


# ===========================================================================
# update_readme — STATE_START/END markers
# ===========================================================================

class TestUpdateReadme:
    def test_state_block_replaced(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        readme = tmp_path / "README.md"
        readme.write_text(
            "# Header\n\n"
            "<!-- STATE_START -->\nold content\n<!-- STATE_END -->\n\n"
            "Footer",
            encoding="utf-8",
        )
        state = {**BASE_STATE, "era": "Industrial Era", "laws_count": 5,
                 "next_tick_at": "2026-06-12 00:00:00"}
        stats = {"proposals_passed": 3, "proposals_rejected": 1}
        tv.update_readme(state, stats, None, "2026-06-11")
        content = readme.read_text(encoding="utf-8")
        assert "<!-- STATE_START -->" in content
        assert "<!-- STATE_END -->" in content
        assert "old content" not in content
        assert "Industrial Era" in content
        assert "2026-06-12 00:00:00" in content

    def test_missing_markers_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        readme = tmp_path / "README.md"
        original = "# No markers here\n"
        readme.write_text(original, encoding="utf-8")
        state = {**BASE_STATE}
        tv.update_readme(state, {}, None, "2026-06-11")
        assert readme.read_text(encoding="utf-8") == original  # unchanged, no crash


# ===========================================================================
# upsert_bot_comment — stored ID PATCH path and POST fallback
# ===========================================================================

class TestUpsertBotComment:
    def test_patch_when_id_stored(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/pinned_comment_ids.json").write_text('{"99": 12345}')
        patched = []
        def fake_run(cmd):
            patched.append(cmd)
            if "--method" in cmd and "PATCH" in cmd:
                return '{"id": 12345}'  # non-empty = success
            return ""
        monkeypatch.setattr(_engine_content, "run", fake_run)
        tv.upsert_bot_comment(99, "hello world")
        patch_calls = [c for c in patched if "PATCH" in c]
        assert len(patch_calls) == 1
        assert any("12345" in part for part in patch_calls[0])

    def test_post_when_no_stored_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        # No pinned_comment_ids.json
        posted = []
        def fake_run(cmd):
            if "issue" in cmd and "comment" in cmd:
                posted.append(cmd)
                return "https://github.com/test/repo/issues/5#issuecomment-9876543"
            return ""
        monkeypatch.setattr(_engine_content, "run", fake_run)
        tv.upsert_bot_comment(5, "new comment body")
        assert len(posted) == 1
        ids = json.loads((tmp_path / "world/pinned_comment_ids.json").read_text())
        assert ids.get("5") == 9876543

    def test_patch_failure_falls_back_to_post(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/pinned_comment_ids.json").write_text('{"7": 111}')
        posted = []
        def fake_run(cmd):
            if "PATCH" in cmd:
                return ""  # empty = PATCH failed
            if "issue" in cmd and "comment" in cmd:
                posted.append(cmd)
                return "https://github.com/test/repo/issues/7#issuecomment-222"
            return ""
        monkeypatch.setattr(_engine_content, "run", fake_run)
        tv.upsert_bot_comment(7, "updated body")
        assert len(posted) == 1
        ids = json.loads((tmp_path / "world/pinned_comment_ids.json").read_text())
        assert ids.get("7") == 222
