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


# ===========================================================================
# generate_narrative
# ===========================================================================

class TestGenerateNarrative:
    def test_calls_llm_with_correct_args(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "A new law was passed. Citizens rejoice."
        )
        with patch.object(_engine_content, "client", mock_client):
            result = tv.generate_narrative("Build a Park", 10, 2, BASE_STATE)
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs[1]["messages"] if call_kwargs[1] else call_kwargs[0][1]
        prompt_text = messages[0]["content"]
        assert "Build a Park" in prompt_text
        assert "10 for" in prompt_text
        assert "2 against" in prompt_text

    def test_returns_string(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "  The law was enacted today.  "
        )
        with patch.object(_engine_content, "client", mock_client):
            result = tv.generate_narrative("New Tax", 5, 1, BASE_STATE)
        assert isinstance(result, str)
        assert result == "The law was enacted today."

    def test_returns_fallback_on_llm_exception(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("LLM unavailable")
        with patch.object(_engine_content, "client", mock_client):
            result = tv.generate_narrative("Fallback Law", 3, 1, BASE_STATE)
        assert "Fallback Law" in result
        assert isinstance(result, str)

    def test_state_metrics_in_prompt(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        state = {**BASE_STATE, "era": "Industrial Era", "laws_count": 42}
        with patch.object(_engine_content, "client", mock_client):
            tv.generate_narrative("Test Law", 8, 4, state)
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs[1]["messages"] if call_kwargs[1] else call_kwargs[0][1]
        prompt_text = messages[0]["content"]
        assert "Industrial Era" in prompt_text
        assert "42" in prompt_text


# ===========================================================================
# update_world_summary
# ===========================================================================

class TestUpdateWorldSummary:
    def test_calls_llm(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "The nation prospers under stable governance."
        )
        with patch.object(_engine_content, "client", mock_client):
            result = tv.update_world_summary(BASE_STATE)
        mock_client.chat.completions.create.assert_called_once()

    def test_returns_string(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "  Stable nation.  "
        )
        with patch.object(_engine_content, "client", mock_client):
            result = tv.update_world_summary(BASE_STATE)
        assert isinstance(result, str)
        assert result == "Stable nation."

    def test_returns_empty_string_on_exception(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = ConnectionError("Network error")
        with patch.object(_engine_content, "client", mock_client):
            result = tv.update_world_summary(BASE_STATE)
        assert result == ""

    def test_state_serialized_in_prompt(self):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = "ok"
        state = {**BASE_STATE, "era": "Space Age"}
        with patch.object(_engine_content, "client", mock_client):
            tv.update_world_summary(state)
        call_kwargs = mock_client.chat.completions.create.call_args
        messages = call_kwargs[1]["messages"] if call_kwargs[1] else call_kwargs[0][1]
        prompt_text = messages[0]["content"]
        assert "Space Age" in prompt_text


# ===========================================================================
# generate_world_md
# ===========================================================================

class TestGenerateWorldMd:
    def _setup_world_dirs(self, tmp_path):
        (tmp_path / "world" / "annals").mkdir(parents=True)
        (tmp_path / "world" / "archive").mkdir(parents=True)
        for cat, _ in [("institutions", "Institutions"), ("districts", "Districts"),
                       ("buildings", "Buildings"), ("sectors", "Sectors")]:
            (tmp_path / "world" / "entities" / cat).mkdir(parents=True)
            (tmp_path / "world" / "entities" / cat / "_index.json").write_text(
                '{"entities": []}', encoding="utf-8"
            )

    def test_writes_world_md(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world_dirs(tmp_path)
        state = {**BASE_STATE}
        tv.generate_world_md(state, law_number=None, date="2026-06-13")
        assert (tmp_path / "world" / "WORLD.md").exists()

    def test_contains_expected_content(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world_dirs(tmp_path)
        state = {**BASE_STATE, "era": "Industrial Era", "laws_count": 5,
                 "treasury": 1234, "currency": "Git Coins"}
        tv.generate_world_md(state, law_number=None, date="2026-06-13")
        content = (tmp_path / "world" / "WORLD.md").read_text(encoding="utf-8")
        assert "# World State" in content
        assert "Industrial Era" in content
        assert "2026-06-13" in content
        assert "1,234 Git Coins" in content

    def test_law_number_link_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world_dirs(tmp_path)
        state = {**BASE_STATE}
        tv.generate_world_md(state, law_number=7, date="2026-06-13")
        content = (tmp_path / "world" / "WORLD.md").read_text(encoding="utf-8")
        assert "Law 007" in content
        assert "laws/law-007.md" in content

    def test_no_law_number_no_link(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world_dirs(tmp_path)
        state = {**BASE_STATE}
        tv.generate_world_md(state, law_number=None, date="2026-06-13")
        content = (tmp_path / "world" / "WORLD.md").read_text(encoding="utf-8")
        assert "laws/law-" not in content
        assert "2026-06-13" in content

    def test_entities_section_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world_dirs(tmp_path)
        state = {**BASE_STATE}
        tv.generate_world_md(state, law_number=None, date="2026-06-13")
        content = (tmp_path / "world" / "WORLD.md").read_text(encoding="utf-8")
        assert "## Entities" in content
        assert "## Archive" in content

    def test_entity_listed_in_output(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world_dirs(tmp_path)
        ent_path = tmp_path / "world" / "entities" / "buildings"
        (ent_path / "_index.json").write_text(
            '{"entities": ["b001"]}', encoding="utf-8"
        )
        (ent_path / "b001.json").write_text(
            json.dumps({"name": "Town Hall", "built_law": 3, "auto_trigger": "—"}),
            encoding="utf-8",
        )
        state = {**BASE_STATE}
        tv.generate_world_md(state, law_number=None, date="2026-06-13")
        content = (tmp_path / "world" / "WORLD.md").read_text(encoding="utf-8")
        assert "Town Hall" in content
        assert "b001" in content


# ===========================================================================
# _badge_url_val
# ===========================================================================

class TestBadgeUrlVal:
    def test_spaces_replaced_with_underscore(self):
        result = _engine_content._badge_url_val("Founding Era")
        assert " " not in result
        assert "Founding_Era" in result

    def test_special_chars_encoded(self):
        result = _engine_content._badge_url_val("hello/world")
        assert "/" not in result

    def test_ampersand_encoded(self):
        result = _engine_content._badge_url_val("War & Peace")
        assert "&" not in result

    def test_plain_string_unchanged(self):
        result = _engine_content._badge_url_val("SimpleValue")
        assert result == "SimpleValue"

    def test_non_string_input_converted(self):
        result = _engine_content._badge_url_val(42)
        assert result == "42"

    def test_empty_string(self):
        result = _engine_content._badge_url_val("")
        assert result == ""


# ===========================================================================
# _load_pinned_ids
# ===========================================================================

class TestLoadPinnedIds:
    def test_reads_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "pinned_comment_ids.json").write_text(
            '{"5": 99, "12": 200}', encoding="utf-8"
        )
        result = tv._load_pinned_ids()
        assert result == {"5": 99, "12": 200}

    def test_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        result = tv._load_pinned_ids()
        assert result == {}

    def test_invalid_json_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "pinned_comment_ids.json").write_text(
            "not valid json", encoding="utf-8"
        )
        result = tv._load_pinned_ids()
        assert result == {}

    def test_empty_file_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "pinned_comment_ids.json").write_text(
            "{}", encoding="utf-8"
        )
        result = tv._load_pinned_ids()
        assert result == {}


# ===========================================================================
# generate_citizen_narrator
# ===========================================================================

class TestGenerateCitizenNarrator:
    def _make_state_file(self, tmp_path, state):
        (tmp_path / "world").mkdir(parents=True, exist_ok=True)
        (tmp_path / "world" / "state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )

    def test_skipped_if_called_recently(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Use a timestamp earlier today (UTC) to confirm same-day cadence skips the run
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        recent_date = f"{today_str}T00:01:00+00:00"
        state = {**BASE_STATE, "last_narrator_date": recent_date}
        self._make_state_file(tmp_path, state)
        mock_client = MagicMock()
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "_get_or_create_citizen_voices_issue",
                          return_value=1):
            tv.generate_citizen_narrator()
        mock_client.chat.completions.create.assert_not_called()

    def test_runs_when_no_last_narrator_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state = {**BASE_STATE}
        self._make_state_file(tmp_path, state)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "**Alice, Official:**\nAll is well.\n\n"
            "**Bob, Worker:**\nHard times.\n\n"
            "**Carol, Teacher:**\nThe children thrive."
        )
        fake_run_calls = []
        def fake_run(cmd):
            fake_run_calls.append(cmd)
            if "issue" in cmd and "comment" in cmd:
                return "https://github.com/test/repo/issues/1#issuecomment-100"
            return ""
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "_get_or_create_citizen_voices_issue",
                          return_value=1), \
             patch.object(_engine_content, "run", fake_run):
            tv.generate_citizen_narrator()
        mock_client.chat.completions.create.assert_called_once()

    def test_runs_when_last_narrator_over_1_day_ago(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        old_date = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        state = {**BASE_STATE, "last_narrator_date": old_date}
        self._make_state_file(tmp_path, state)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "Citizen voices narrative."
        )
        def fake_run(cmd):
            if "issue" in cmd and "comment" in cmd:
                return "https://github.com/test/repo/issues/2#issuecomment-200"
            return ""
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "_get_or_create_citizen_voices_issue",
                          return_value=2), \
             patch.object(_engine_content, "run", fake_run):
            tv.generate_citizen_narrator()
        mock_client.chat.completions.create.assert_called_once()

    def test_returns_early_on_llm_exception(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state = {**BASE_STATE}
        self._make_state_file(tmp_path, state)
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = RuntimeError("LLM down")
        mock_get_issue = MagicMock(return_value=1)
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "_get_or_create_citizen_voices_issue",
                          mock_get_issue):
            tv.generate_citizen_narrator()
        mock_get_issue.assert_not_called()

    def test_narrator_issue_updated_when_successful(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state = {**BASE_STATE}
        self._make_state_file(tmp_path, state)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "Three voices speak."
        )
        comment_calls = []
        def fake_run(cmd):
            comment_calls.append(cmd)
            if "issue" in cmd and "comment" in cmd:
                return "https://github.com/test/repo/issues/3#issuecomment-300"
            return ""
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "_get_or_create_citizen_voices_issue",
                          return_value=3), \
             patch.object(_engine_content, "run", fake_run):
            tv.generate_citizen_narrator()
        posted = [c for c in comment_calls if "issue" in c and "comment" in c]
        assert len(posted) == 1

    def test_state_updated_with_narrator_date(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        state = {**BASE_STATE}
        self._make_state_file(tmp_path, state)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "Today's voices."
        )
        def fake_run(cmd):
            if "issue" in cmd and "comment" in cmd:
                return "https://github.com/test/repo/issues/4#issuecomment-400"
            return ""
        with patch.object(_engine_content, "client", mock_client), \
             patch.object(_engine_content, "_get_or_create_citizen_voices_issue",
                          return_value=4), \
             patch.object(_engine_content, "run", fake_run):
            tv.generate_citizen_narrator()
        updated = json.loads((tmp_path / "world" / "state.json").read_text())
        assert "last_narrator_date" in updated


# ===========================================================================
# generate_citizen_narrator — UTC calendar date cadence
# ===========================================================================

class TestCitizenNarratorDailyCadence:

    def _fake_client(self, response_text="diary content"):
        """Build a minimal LLM client stub that returns response_text."""
        class FakeMsg:
            content = response_text
        class FakeChoice:
            message = FakeMsg()
        class FakeCompletion:
            choices = [FakeChoice()]
        class FakeCompletions:
            def create(self, *a, **kw):
                return FakeCompletion()
        class FakeChat:
            completions = FakeCompletions()
        class FakeClient:
            chat = FakeChat()
        return FakeClient()

    def test_skips_when_already_ran_today(self, monkeypatch):
        """No LLM call when last_narrator_date is today (UTC)."""
        import scripts.engine.content as content
        from datetime import datetime, timezone

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state = {"last_narrator_date": f"{today}T06:00:00+00:00"}

        called = []
        class FakeCompletions:
            def create(self, *a, **kw):
                called.append(True)
                raise AssertionError("LLM must not be called when narrator already ran today")
        class FakeChat:
            completions = FakeCompletions()
        class FakeClient:
            chat = FakeChat()

        monkeypatch.setattr(content, "read_state", lambda: dict(state))
        monkeypatch.setattr(content, "client", FakeClient())
        content.generate_citizen_narrator()
        assert not called

    def test_fires_on_different_utc_date(self, monkeypatch):
        """LLM is called when last_narrator_date is a previous UTC date."""
        import scripts.engine.content as content
        from datetime import datetime, timezone, timedelta

        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        state_store = {"last_narrator_date": f"{yesterday}T23:59:00+00:00"}
        written = {}

        monkeypatch.setattr(content, "read_state", lambda: dict(state_store))
        monkeypatch.setattr(content, "write_state", lambda s: written.update(s))
        monkeypatch.setattr(content, "_get_or_create_citizen_voices_issue", lambda: 0)
        monkeypatch.setattr(content, "upsert_bot_comment", lambda n, b: None)
        monkeypatch.setattr(content, "client", self._fake_client())

        content.generate_citizen_narrator()
        assert "last_narrator_date" in written, "last_narrator_date must be updated after narration"

    def test_fires_when_no_previous_date(self, monkeypatch):
        """Fires on first run when last_narrator_date is absent."""
        import scripts.engine.content as content

        state_store = {}
        written = {}
        monkeypatch.setattr(content, "read_state", lambda: dict(state_store))
        monkeypatch.setattr(content, "write_state", lambda s: written.update(s))
        monkeypatch.setattr(content, "_get_or_create_citizen_voices_issue", lambda: 0)
        monkeypatch.setattr(content, "upsert_bot_comment", lambda n, b: None)
        monkeypatch.setattr(content, "client", self._fake_client())

        content.generate_citizen_narrator()
        assert "last_narrator_date" in written

    def test_skips_with_malformed_date_gracefully(self, monkeypatch):
        """Malformed last_narrator_date is treated as absent -- narrator fires."""
        import scripts.engine.content as content

        state_store = {"last_narrator_date": "not-a-date"}
        written = {}
        monkeypatch.setattr(content, "read_state", lambda: dict(state_store))
        monkeypatch.setattr(content, "write_state", lambda s: written.update(s))
        monkeypatch.setattr(content, "_get_or_create_citizen_voices_issue", lambda: 0)
        monkeypatch.setattr(content, "upsert_bot_comment", lambda n, b: None)
        monkeypatch.setattr(content, "client", self._fake_client())

        content.generate_citizen_narrator()
        # Should fire (malformed = treat as no prior run)
        assert "last_narrator_date" in written


# ===========================================================================
# generate_world_md — extra state fields and missing entity index (lines 92, 98-99)
# ===========================================================================

class TestGenerateWorldMdEdgeCases:
    def _setup_world_dirs(self, tmp_path):
        (tmp_path / "world" / "annals").mkdir(parents=True)
        (tmp_path / "world" / "archive").mkdir(parents=True)
        for cat, _ in [("institutions", ""), ("districts", ""),
                       ("buildings", ""), ("sectors", "")]:
            (tmp_path / "world" / "entities" / cat).mkdir(parents=True)
            (tmp_path / "world" / "entities" / cat / "_index.json").write_text(
                '{"entities": []}', encoding="utf-8")

    def test_extra_state_field_shown_in_metrics(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world_dirs(tmp_path)
        state = {**BASE_STATE, "custom_extra_stat": 999}
        tv.generate_world_md(state, law_number=None, date="2026-06-13")
        content = (tmp_path / "world" / "WORLD.md").read_text(encoding="utf-8")
        assert "Custom Extra Stat" in content
        assert "999" in content

    def test_missing_entity_index_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world" / "archive").mkdir(parents=True)
        state = {**BASE_STATE}
        tv.generate_world_md(state, law_number=None, date="2026-06-13")
        content = (tmp_path / "world" / "WORLD.md").read_text(encoding="utf-8")
        assert "## Entities" in content

    def test_archive_oserror_handled(self, tmp_path, monkeypatch):
        from pathlib import Path as _Path
        monkeypatch.chdir(tmp_path)
        self._setup_world_dirs(tmp_path)
        state = {**BASE_STATE}
        original_glob = _Path.glob
        def patched_glob(self, pattern):
            if "archive" in str(self) and pattern == "*.json":
                raise OSError("Permission denied")
            return original_glob(self, pattern)
        monkeypatch.setattr(_Path, "glob", patched_glob)
        tv.generate_world_md(state, law_number=None, date="2026-06-13")
        content = (tmp_path / "world" / "WORLD.md").read_text(encoding="utf-8")
        assert "## Archive" in content

    def test_archived_entities_listed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world_dirs(tmp_path)
        archive_entity = {"name": "Old Mill", "demolished_law": 3, "auto_reason": "Outdated"}
        (tmp_path / "world" / "archive" / "bld-001.json").write_text(
            json.dumps(archive_entity))
        state = {**BASE_STATE}
        tv.generate_world_md(state, law_number=None, date="2026-06-13")
        content = (tmp_path / "world" / "WORLD.md").read_text(encoding="utf-8")
        assert "Old Mill" in content
        assert "Outdated" in content


# ===========================================================================
# generate_annals — LLM exception returns early (lines 217-219)
# ===========================================================================

class TestGenerateAnnalsLlmException:
    def test_llm_exception_no_chapter_written(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world/annals").mkdir(parents=True)
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        history = [{"tick": i + 1, "laws_count": 0, "population": 1000,
                    "treasury": 0, "era": "Founding Era"} for i in range(10)]
        mc = MagicMock()
        mc.chat.completions.create.side_effect = RuntimeError("LLM failed")
        with patch.object(_engine_content, "client", mc):
            tv.generate_annals(history)
        assert not (tmp_path / "world/annals/chapter-001.md").exists()


# ===========================================================================
# upsert_bot_comment — result without issuecomment ID (line 267)
# ===========================================================================

class TestUpsertBotCommentNoCommentId:
    def test_post_without_issuecomment_in_result(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        monkeypatch.setattr(_engine_content, "run", lambda cmd: "")
        tv.upsert_bot_comment(10, "body text")
        assert not (tmp_path / "world/pinned_comment_ids.json").exists()


# ===========================================================================
# _get_or_create_citizen_voices_issue (lines 271-295)
# ===========================================================================

class TestGetOrCreateCitizenVoicesIssue:
    def test_returns_existing_issue_number(self, monkeypatch):
        monkeypatch.setattr(_engine_content, "run", lambda cmd: "")
        monkeypatch.setattr(_engine_content, "gh_json", lambda cmd: [{"number": 55}])
        result = tv._get_or_create_citizen_voices_issue()
        assert result == 55

    def test_creates_new_issue_when_none_exist(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        run_calls = iter(["", "https://github.com/test/repo/issues/88"])
        monkeypatch.setattr(_engine_content, "run", lambda cmd: next(run_calls))
        monkeypatch.setattr(_engine_content, "gh_json", lambda cmd: [])
        result = tv._get_or_create_citizen_voices_issue()
        assert result == 88

    def test_returns_zero_on_bad_url(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "scripts").mkdir()
        run_calls = iter(["", "not-a-valid-url"])
        monkeypatch.setattr(_engine_content, "run", lambda cmd: next(run_calls))
        monkeypatch.setattr(_engine_content, "gh_json", lambda cmd: [])
        result = tv._get_or_create_citizen_voices_issue()
        assert result == 0


# ===========================================================================
# generate_citizen_narrator — naive datetime gets UTC timezone (line 305)
# ===========================================================================

class TestCitizenNarratorNaiveDatetime:
    def test_naive_datetime_assigned_utc_and_skips_today(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        today_naive = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        state = {**BASE_STATE, "last_narrator_date": today_naive}
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(state))
        mc = MagicMock()
        with patch.object(_engine_content, "client", mc):
            tv.generate_citizen_narrator()
        mc.chat.completions.create.assert_not_called()
