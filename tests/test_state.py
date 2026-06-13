import json
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure scripts/ is on sys.path so engine.state can be imported directly.
# helpers.py already appends scripts/ when it imports tally_votes.
# ---------------------------------------------------------------------------
from tests.helpers import BASE_STATE  # noqa: F401 — triggers sys.path setup

import engine.state as state_mod


# ===========================================================================
# TestReadJson
# ===========================================================================

class TestReadJson:
    def test_reads_valid_json(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text(json.dumps({"key": "value"}), encoding="utf-8")
        result = state_mod.read_json(f)
        assert result == {"key": "value"}

    def test_reads_nested_json(self, tmp_path):
        payload = {"a": [1, 2, 3], "b": {"c": True}}
        f = tmp_path / "nested.json"
        f.write_text(json.dumps(payload), encoding="utf-8")
        assert state_mod.read_json(f) == payload

    def test_missing_file_raises(self, tmp_path):
        missing = tmp_path / "no_such_file.json"
        with pytest.raises(FileNotFoundError):
            state_mod.read_json(missing)

    def test_corrupted_json_raises(self, tmp_path):
        f = tmp_path / "bad.json"
        f.write_text("not valid json {{{", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            state_mod.read_json(f)


# ===========================================================================
# TestWriteJson
# ===========================================================================

class TestWriteJson:
    def test_creates_file_with_correct_content(self, tmp_path):
        f = tmp_path / "out.json"
        state_mod.write_json(f, {"x": 1})
        result = json.loads(f.read_text(encoding="utf-8"))
        assert result == {"x": 1}

    def test_file_ends_with_newline(self, tmp_path):
        f = tmp_path / "out.json"
        state_mod.write_json(f, {})
        assert f.read_text(encoding="utf-8").endswith("\n")

    def test_overwrites_existing_file(self, tmp_path):
        f = tmp_path / "out.json"
        f.write_text(json.dumps({"old": True}), encoding="utf-8")
        state_mod.write_json(f, {"new": True})
        result = json.loads(f.read_text(encoding="utf-8"))
        assert result == {"new": True}
        assert "old" not in result

    def test_writes_indented_output(self, tmp_path):
        f = tmp_path / "out.json"
        state_mod.write_json(f, {"a": 1})
        raw = f.read_text(encoding="utf-8")
        # indent=2 means the file must have at least one newline inside
        assert "\n" in raw


# ===========================================================================
# TestReadState
# ===========================================================================

class TestReadState:
    def test_returns_dict_from_state_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "state.json").write_text(
            json.dumps(BASE_STATE), encoding="utf-8"
        )
        result = state_mod.read_state()
        assert result == BASE_STATE

    def test_missing_state_file_raises(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        with pytest.raises(FileNotFoundError):
            state_mod.read_state()

    def test_returns_all_base_state_keys(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "state.json").write_text(
            json.dumps(BASE_STATE), encoding="utf-8"
        )
        result = state_mod.read_state()
        for key in BASE_STATE:
            assert key in result


# ===========================================================================
# TestWriteState
# ===========================================================================

class TestWriteState:
    def test_writes_to_world_state_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        state_mod.write_state(BASE_STATE)
        written = json.loads(
            (tmp_path / "world" / "state.json").read_text(encoding="utf-8")
        )
        assert written == BASE_STATE

    def test_overwrites_existing_state(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "state.json").write_text(
            json.dumps({"era": "Old Era"}), encoding="utf-8"
        )
        new_state = {**BASE_STATE, "era": "Modern Era"}
        state_mod.write_state(new_state)
        written = json.loads(
            (tmp_path / "world" / "state.json").read_text(encoding="utf-8")
        )
        assert written["era"] == "Modern Era"


# ===========================================================================
# TestReadStats
# ===========================================================================

class TestReadStats:
    def test_returns_dict_from_stats_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        stats = {"proposals_total": 5, "proposals_passed": 3,
                 "proposals_rejected": 1, "proposals_silent": 1}
        (tmp_path / "world" / "stats.json").write_text(
            json.dumps(stats), encoding="utf-8"
        )
        result = state_mod.read_stats()
        assert result == stats

    def test_missing_file_returns_default_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        result = state_mod.read_stats()
        assert result == {
            "proposals_total": 0,
            "proposals_passed": 0,
            "proposals_rejected": 0,
            "proposals_silent": 0,
        }

    def test_default_dict_has_all_keys(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        result = state_mod.read_stats()
        for key in ("proposals_total", "proposals_passed",
                    "proposals_rejected", "proposals_silent"):
            assert key in result


# ===========================================================================
# TestWriteStats
# ===========================================================================

class TestWriteStats:
    def test_writes_to_world_stats_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        stats = {"proposals_total": 10, "proposals_passed": 7,
                 "proposals_rejected": 2, "proposals_silent": 1}
        state_mod.write_stats(stats)
        written = json.loads(
            (tmp_path / "world" / "stats.json").read_text(encoding="utf-8")
        )
        assert written == stats

    def test_overwrites_existing_stats(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "stats.json").write_text(
            json.dumps({"proposals_total": 1}), encoding="utf-8"
        )
        state_mod.write_stats({"proposals_total": 99, "proposals_passed": 0,
                                "proposals_rejected": 0, "proposals_silent": 0})
        written = json.loads(
            (tmp_path / "world" / "stats.json").read_text(encoding="utf-8")
        )
        assert written["proposals_total"] == 99


# ===========================================================================
# TestLoadEventPool
# ===========================================================================

class TestLoadEventPool:
    def test_returns_list_from_event_pool_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        pool = [{"id": "evt-1"}, {"id": "evt-2"}]
        (tmp_path / "world" / "event_pool.json").write_text(
            json.dumps(pool), encoding="utf-8"
        )
        result = state_mod.load_event_pool()
        assert result == pool

    def test_missing_file_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        result = state_mod.load_event_pool()
        assert result == []

    def test_returns_all_events_in_pool(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        pool = [{"id": f"evt-{i}"} for i in range(5)]
        (tmp_path / "world" / "event_pool.json").write_text(
            json.dumps(pool), encoding="utf-8"
        )
        result = state_mod.load_event_pool()
        assert len(result) == 5


# ===========================================================================
# TestLoadActiveEvent
# ===========================================================================

class TestLoadActiveEvent:
    def test_returns_dict_from_active_event_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        event = {"id": "evt-active", "title": "Storm", "duration_hours": 4}
        (tmp_path / "world" / "active_event.json").write_text(
            json.dumps(event), encoding="utf-8"
        )
        result = state_mod.load_active_event()
        assert result == event

    def test_missing_file_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        result = state_mod.load_active_event()
        assert result == {}

    def test_empty_object_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "active_event.json").write_text(
            "{}", encoding="utf-8"
        )
        result = state_mod.load_active_event()
        assert result == {}

    def test_corrupted_json_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "active_event.json").write_text(
            "not valid json >>>", encoding="utf-8"
        )
        result = state_mod.load_active_event()
        assert result == {}

    def test_non_dict_value_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "active_event.json").write_text(
            "[1, 2, 3]", encoding="utf-8"
        )
        result = state_mod.load_active_event()
        assert result == {}


# ===========================================================================
# TestSaveActiveEvent
# ===========================================================================

class TestSaveActiveEvent:
    def test_writes_dict_to_active_event_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        event = {"id": "evt-1", "title": "Flood", "duration_hours": 4}
        state_mod.save_active_event(event)
        written = json.loads(
            (tmp_path / "world" / "active_event.json").read_text(encoding="utf-8")
        )
        assert written == event

    def test_overwrites_existing_active_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "active_event.json").write_text(
            json.dumps({"id": "old-event"}), encoding="utf-8"
        )
        new_event = {"id": "new-event", "title": "Drought"}
        state_mod.save_active_event(new_event)
        written = json.loads(
            (tmp_path / "world" / "active_event.json").read_text(encoding="utf-8")
        )
        assert written["id"] == "new-event"

    def test_saves_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        state_mod.save_active_event({})
        written = json.loads(
            (tmp_path / "world" / "active_event.json").read_text(encoding="utf-8")
        )
        assert written == {}

    def test_output_ends_with_newline(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        state_mod.save_active_event({"id": "x"})
        raw = (tmp_path / "world" / "active_event.json").read_text(encoding="utf-8")
        assert raw.endswith("\n")


# ===========================================================================
# TestReadHistory
# ===========================================================================

class TestReadHistory:
    def test_returns_list_from_history_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        history = [{"tick": 1, "era": "Founding Era"}, {"tick": 2, "era": "Industrial Era"}]
        (tmp_path / "world" / "history.json").write_text(
            json.dumps(history), encoding="utf-8"
        )
        result = state_mod.read_history()
        assert result == history

    def test_missing_file_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        result = state_mod.read_history()
        assert result == []

    def test_corrupted_json_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world" / "history.json").write_text(
            "not valid json <<<", encoding="utf-8"
        )
        result = state_mod.read_history()
        assert result == []

    def test_preserves_tick_order(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        history = [{"tick": i} for i in range(1, 6)]
        (tmp_path / "world" / "history.json").write_text(
            json.dumps(history), encoding="utf-8"
        )
        result = state_mod.read_history()
        ticks = [entry["tick"] for entry in result]
        assert ticks == list(range(1, 6))
