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
# append_history_snapshot
# ===========================================================================

class TestAppendHistorySnapshot:
    def test_snapshot_fields(self):
        written = []
        with patch.object(tv, "load_active_event", return_value={}), \
             patch("pathlib.Path.read_text", return_value="[]"), \
             patch("pathlib.Path.write_text", side_effect=lambda text, **kw: written.append(json.loads(text))):
            tv.append_history_snapshot(BASE_STATE)
        assert len(written) == 1
        snap = written[0][0]
        assert snap["tick"] == 1
        assert snap["education"] == BASE_STATE["education"]
        assert snap["era"] == BASE_STATE["era"]
        assert snap["active_event"] is None

    def test_snapshot_capped_at_100(self):
        existing = [{"tick": i} for i in range(1, 101)]  # 100 entries
        written = []
        with patch.object(tv, "load_active_event", return_value={}), \
             patch("pathlib.Path.read_text", return_value=json.dumps(existing)), \
             patch("pathlib.Path.write_text", side_effect=lambda text, **kw: written.append(json.loads(text))):
            tv.append_history_snapshot(BASE_STATE)
        result = written[0]
        assert len(result) == 100
        assert result[-1]["tick"] == 101  # new snapshot appended, oldest dropped


# ===========================================================================
# append_history_snapshot — tick counter correctness after truncation
# ===========================================================================

class TestAppendHistorySnapshotTick:
    def test_first_tick_is_1(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        (tmp_path / "world/active_event.json").write_text("{}")
        tv.append_history_snapshot(BASE_STATE)
        hist = json.loads((tmp_path / "world/history.json").read_text())
        assert hist[0]["tick"] == 1

    def test_tick_increments_sequentially(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        (tmp_path / "world/active_event.json").write_text("{}")
        for _ in range(5):
            tv.append_history_snapshot(BASE_STATE)
        hist = json.loads((tmp_path / "world/history.json").read_text())
        assert [h["tick"] for h in hist] == [1, 2, 3, 4, 5]

    def test_tick_continues_after_100_entry_truncation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        (tmp_path / "world/active_event.json").write_text("{}")
        # Seed history with 100 entries (ticks 1–100)
        history = [{"tick": i + 1, "era": "Founding Era", "laws_count": 0,
                    "population": 1000, "treasury": 0, "education": 0,
                    "industry": 0, "welfare": 0, "green_policy": 0, "defense": 0,
                    "pollution": 0, "stability": 79, "active_event": None,
                    "date": "2026-01-01T00:00:00Z"} for i in range(100)]
        (tmp_path / "world/history.json").write_text(json.dumps(history))
        tv.append_history_snapshot(BASE_STATE)
        hist = json.loads((tmp_path / "world/history.json").read_text())
        assert len(hist) == 100       # still capped at 100
        assert hist[-1]["tick"] == 101  # correctly incremented, not reset


# ===========================================================================
# collect_star_income — per-login tracking (anti-star-washing)
# ===========================================================================

class TestCollectStarIncome:
    def _setup(self, tmp_path, state_extra=None):
        (tmp_path / "world").mkdir()
        state = {**BASE_STATE, "treasury": 200, **(state_extra or {})}
        (tmp_path / "world/state.json").write_text(json.dumps(state))
        (tmp_path / "world/stats.json").write_text(json.dumps({}))

    def _run(self, tmp_path, monkeypatch, stargazers_output):
        monkeypatch.chdir(tmp_path)
        calls = []
        def fake_run(cmd):
            if "stargazers" in " ".join(cmd):
                return stargazers_output
            calls.append(cmd)
            return ""
        with patch.object(_engine_chronicle, "run", side_effect=fake_run):
            tv.collect_star_income()
        return calls

    def test_first_run_initializes_no_income(self, tmp_path, monkeypatch):
        self._setup(tmp_path)
        self._run(tmp_path, monkeypatch, "alice\nbob\n")
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 200  # no income on first run
        assert set(state["known_stargazers"]) == {"alice", "bob"}

    def test_new_star_earns_income(self, tmp_path, monkeypatch):
        self._setup(tmp_path, {"known_stargazers": ["alice"]})
        self._run(tmp_path, monkeypatch, "alice\nbob\n")
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 210  # bob is new: +10 GC
        assert set(state["known_stargazers"]) == {"alice", "bob"}

    def test_restar_earns_no_income(self, tmp_path, monkeypatch):
        # alice starred, unstarred (removed from current), then re-starred
        # ever_starred still contains alice → no income
        self._setup(tmp_path, {"known_stargazers": ["alice", "bob"]})
        self._run(tmp_path, monkeypatch, "alice\n")  # bob unstarred, alice re-starred
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 200  # no new income (alice was already known)

    def test_no_stars_no_income(self, tmp_path, monkeypatch):
        self._setup(tmp_path, {"known_stargazers": ["alice"]})
        self._run(tmp_path, monkeypatch, "alice\n")  # same stargazers
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 200

    def test_treasury_capped_at_100000(self, tmp_path, monkeypatch):
        self._setup(tmp_path, {"treasury": 99_995, "known_stargazers": []})
        self._run(tmp_path, monkeypatch, "\n".join(f"u{i}" for i in range(10)))
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 100_000  # capped

    def test_empty_stargazers_no_crash(self, tmp_path, monkeypatch):
        self._setup(tmp_path, {"known_stargazers": ["alice"]})
        self._run(tmp_path, monkeypatch, "")  # no output (0 stars)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 200  # no income, no crash


# ===========================================================================
# save_dispatch — idempotency and file writes
# ===========================================================================

class TestSaveDispatch:
    def _mock_llm(self, monkeypatch):
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value.choices[0].message.content = (
            "Test narrative."
        )
        monkeypatch.setattr(_engine_chronicle, "client", mock_client)
        return mock_client

    def test_saves_dispatch_to_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/dispatches.json").write_text("[]")
        (tmp_path / "world/history.json").write_text(
            json.dumps([{"tick": 5, "date": "2026-06-11T00:00:00Z"}])
        )
        self._mock_llm(monkeypatch)
        tv.save_dispatch({**BASE_STATE}, True, 0, "", 0)
        dispatches = json.loads((tmp_path / "world/dispatches.json").read_text())
        assert len(dispatches) == 1
        assert dispatches[0]["tick"] == 6

    def test_idempotent_skips_duplicate_tick(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/history.json").write_text(
            json.dumps([{"tick": 9, "date": "2026-06-11T00:00:00Z"}])
        )
        # dispatches.json already has tick 10
        existing = [{"tick": 10, "date": "2026-06-11", "narrative": "Old.",
                     "changes": "quiet tick", "metrics": "pop 1000"}]
        (tmp_path / "world/dispatches.json").write_text(json.dumps(existing))
        mock_client = self._mock_llm(monkeypatch)
        tv.save_dispatch({**BASE_STATE}, False, 0, "", 0)
        # LLM must not be called; dispatches.json must be unchanged
        mock_client.chat.completions.create.assert_not_called()
        dispatches = json.loads((tmp_path / "world/dispatches.json").read_text())
        assert len(dispatches) == 1
        assert dispatches[0]["narrative"] == "Old."

    def test_caps_at_ten_entries(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        existing = [{"tick": i, "date": "2026-06-11", "narrative": f"N{i}.",
                     "changes": "quiet tick", "metrics": ""} for i in range(1, 11)]
        (tmp_path / "world/dispatches.json").write_text(json.dumps(existing))
        (tmp_path / "world/history.json").write_text(
            json.dumps([{"tick": 10, "date": "2026-06-11T00:00:00Z"}])
        )
        self._mock_llm(monkeypatch)
        tv.save_dispatch({**BASE_STATE}, True, 0, "", 0)
        dispatches = json.loads((tmp_path / "world/dispatches.json").read_text())
        assert len(dispatches) == 10
        assert dispatches[-1]["tick"] == 11


# ===========================================================================
# publish_dispatch — only posts to GitHub, reads from dispatches.json
# ===========================================================================

class TestPublishDispatch:
    def test_calls_upsert_with_chronicle_body(self, monkeypatch):
        posted = []
        monkeypatch.setattr(_engine_chronicle, "get_or_create_dispatch_issue",
                            lambda: 42)
        monkeypatch.setattr(_engine_chronicle, "upsert_bot_comment",
                            lambda issue, body: posted.append((issue, body)))
        monkeypatch.setattr(_engine_chronicle, "_build_chronicle_body",
                            lambda: "BODY")
        tv.publish_dispatch()
        assert posted == [(42, "BODY")]

    def test_skips_when_no_issue(self, monkeypatch):
        posted = []
        monkeypatch.setattr(_engine_chronicle, "get_or_create_dispatch_issue",
                            lambda: 0)
        monkeypatch.setattr(_engine_chronicle, "upsert_bot_comment",
                            lambda issue, body: posted.append((issue, body)))
        tv.publish_dispatch()
        assert posted == []


# ===========================================================================
# push_with_retry — retry logic and return value
# ===========================================================================

class TestPushWithRetry:
    def _make_result(self, returncode, stderr=""):
        r = MagicMock()
        r.returncode = returncode
        r.stderr = stderr
        return r

    def test_returns_true_on_first_success(self, monkeypatch):
        monkeypatch.setattr(_engine_gh, "run", lambda cmd: "")
        with patch("engine.gh.subprocess.run",
                   return_value=self._make_result(0)) as mock_push:
            result = tv.push_with_retry()
        assert result is True
        assert mock_push.call_count == 1

    def test_retries_on_failure_and_succeeds(self, monkeypatch):
        monkeypatch.setattr(_engine_gh, "run", lambda cmd: "")
        monkeypatch.setattr(_engine_gh, "time", MagicMock())
        calls = []
        def fake_push(cmd, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                return self._make_result(1, "rejected")
            return self._make_result(0)
        with patch("engine.gh.subprocess.run", side_effect=fake_push):
            result = tv.push_with_retry(max_attempts=3)
        assert result is True
        assert len(calls) == 3

    def test_returns_false_after_all_attempts_fail(self, monkeypatch):
        monkeypatch.setattr(_engine_gh, "run", lambda cmd: "")
        monkeypatch.setattr(_engine_gh, "time", MagicMock())
        with patch("engine.gh.subprocess.run",
                   return_value=self._make_result(1, "remote rejected")):
            result = tv.push_with_retry(max_attempts=2)
        assert result is False


# ===========================================================================
# append_history — FileNotFoundError guard
# ===========================================================================

class TestAppendHistoryGuard:
    def test_missing_index_no_crash(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        # history/INDEX.md does NOT exist
        tv.append_history(1, "Test Law", 42, 3, 1, True, "2026-06-11")
        out = capsys.readouterr().out
        assert "WARN" in out  # warning printed, no exception


# ===========================================================================
# update_laws_index — JSONDecodeError guard
# ===========================================================================

class TestUpdateLawsIndexGuard:
    def test_corrupted_json_resets_gracefully(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/laws_index.json").write_text("NOT_JSON")
        # Should not raise; instead starts fresh
        tv.update_laws_index(5, "Test", 10, "http://x", "Founding Era", "2026-06-11")
        data = json.loads((tmp_path / "world/laws_index.json").read_text())
        assert len(data) == 1


# ===========================================================================
# _build_chronicle_body — handles missing metrics, includes representatives
# ===========================================================================

class TestBuildChronicleBody:
    def test_empty_dispatches_returns_string(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/dispatches.json").write_text("[]")
        body = tv._build_chronicle_body()
        assert "World Chronicle" in body

    def test_missing_metrics_field_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        # Old-format entry without 'metrics' key
        dispatches = [{"tick": 5, "date": "2026-06-11",
                       "narrative": "Test narrative.", "changes": "tick applied"}]
        (tmp_path / "world/dispatches.json").write_text(json.dumps(dispatches))
        body = tv._build_chronicle_body()
        assert "Test narrative" in body

    def test_includes_last_5_dispatches(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        dispatches = [
            {"tick": i, "date": "2026-06-11", "narrative": f"Narrative {i}.",
             "changes": "tick", "metrics": f"pop {i*100}"}
            for i in range(1, 9)
        ]
        (tmp_path / "world/dispatches.json").write_text(json.dumps(dispatches))
        body = tv._build_chronicle_body()
        # Should contain ticks 4-8 (last 5 of 8), not tick 1
        assert "Narrative 8" in body
        assert "Narrative 4" in body
        assert "Narrative 1" not in body

    def test_includes_representatives_when_present(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/dispatches.json").write_text("[]")
        reps = {"selected_at": "2026-06-11", "next_selection": "2026-06-18",
                "representatives": ["alice", "bob"]}
        (tmp_path / "world/representatives.json").write_text(json.dumps(reps))
        body = tv._build_chronicle_body()
        assert "@alice" in body
        assert "@bob" in body

    def test_no_representatives_file_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/dispatches.json").write_text("[]")
        # No representatives.json
        body = tv._build_chronicle_body()
        assert "World Chronicle" in body


# ===========================================================================
# _load_entity_names
# ===========================================================================

class TestLoadEntityNames:
    def test_returns_names_from_all_categories(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Public School", "built_law": 1,
             "built_at": "", "auto_trigger": ""},
        ])
        _make_category(tmp_path, "districts", [
            {"id": "dst-001", "name": "City Park", "built_law": 2,
             "built_at": "", "auto_trigger": ""},
        ])
        _make_category(tmp_path, "institutions")
        _make_category(tmp_path, "sectors")
        names = tv._load_entity_names()
        assert "public school" in names
        assert "city park" in names

    def test_missing_category_dir_skipped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world" / "entities").mkdir(parents=True)
        names = tv._load_entity_names()
        assert names == set()

    def test_names_are_lowercase(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "National University", "built_law": 1,
             "built_at": "", "auto_trigger": ""},
        ])
        _make_category(tmp_path, "districts")
        _make_category(tmp_path, "institutions")
        _make_category(tmp_path, "sectors")
        names = tv._load_entity_names()
        assert "national university" in names
        assert "National University" not in names


# ===========================================================================
# _build_gap_dashboard
# ===========================================================================

class TestBuildGapDashboard:
    def _state(self, **overrides):
        return {**BASE_STATE, "tags_applied": [], **overrides}

    def test_shows_closest_unbuilt_entity(self):
        state = self._state(green_policy=62)
        result = tv._build_gap_dashboard(state, entity_names=set())
        assert "Nature Reserve" in result
        assert "+3" in result

    def test_built_entity_not_shown_as_pending(self):
        state = self._state(green_policy=62)
        result = tv._build_gap_dashboard(state, entity_names={"nature reserve"})
        assert "Nature Reserve" not in result

    def test_at_risk_entity_shown(self):
        state = self._state(green_policy=30)
        result = tv._build_gap_dashboard(state, entity_names={"nature reserve"})
        assert "Nature Reserve" in result
        assert "at risk" in result

    def test_entity_safely_built_not_at_risk(self):
        state = self._state(green_policy=70)
        result = tv._build_gap_dashboard(state, entity_names={"nature reserve"})
        assert "at risk" not in result

    def test_milestone_near_threshold_shown(self):
        state = self._state(industry=44, tags_applied=[])
        result = tv._build_gap_dashboard(state, entity_names=set())
        assert "milestone/industrial-age" in result

    def test_milestone_already_applied_not_shown(self):
        state = self._state(industry=44, tags_applied=["milestone/industrial-age"])
        result = tv._build_gap_dashboard(state, entity_names=set())
        assert "milestone/industrial-age" not in result

    def test_milestone_gap_above_10_not_shown(self):
        state = self._state(industry=30, tags_applied=[])
        result = tv._build_gap_dashboard(state, entity_names=set())
        assert "milestone/industrial-age" not in result

    def test_empty_section_shows_fallback(self):
        all_tag_names = [t[3] for t in tv.THRESHOLD_TAGS]
        # pollution=0 means Smog Zone doesn't exist — exclude it from entity_names
        # so there's no at-risk entity and no pending entity to show
        state = {
            "education": 100, "industry": 100, "welfare": 100,
            "green_policy": 100, "defense": 100, "pollution": 0,
            "stability": 100, "tags_applied": all_tag_names,
        }
        all_entity_names = {r[3].strip().lower() for r in tv.WORLD_GENERATION_RULES
                            if r[0] != "pollution"}
        result = tv._build_gap_dashboard(state, entity_names=all_entity_names)
        assert "All near-threshold goals reached" in result

    def test_shows_at_most_3_pending(self):
        state = {**BASE_STATE, "education": 0, "industry": 0, "welfare": 0,
                 "green_policy": 0, "defense": 0, "pollution": 0,
                 "tags_applied": []}
        result = tv._build_gap_dashboard(state, entity_names=set())
        pending_lines = [line for line in result.splitlines() if "needs +" in line]
        assert len(pending_lines) <= 3

    def test_returns_string_with_header(self):
        result = tv._build_gap_dashboard(self._state(), entity_names=set())
        assert "What Needs Your Vote" in result
        assert isinstance(result, str)


# ===========================================================================
# write_gap_dashboard_json
# ===========================================================================

class TestWriteGapDashboardJson:
    def _state(self, **overrides):
        base = {**BASE_STATE, "tags_applied": []}
        base.update(overrides)
        return base

    def _make_world(self, tmp_path):
        (tmp_path / "world").mkdir()
        for cat in ("buildings", "districts", "institutions", "sectors"):
            cat_path = tmp_path / "world/entities" / cat
            cat_path.mkdir(parents=True)
            (cat_path / "_index.json").write_text(
                json.dumps({"next_seq": 1, "count": 0, "entities": []}))

    def test_gap_json_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        state = self._state(education=40)
        tv.write_gap_dashboard_json(state)
        data = json.loads((tmp_path / "world/gap_dashboard.json").read_text())
        assert "pending" in data
        names = [e["name"] for e in data["pending"]]
        assert any("University" in n or "School" in n for n in names), \
            f"Expected education-related entity in pending, got: {names}"

    def test_milestones_achieved_from_tags_applied(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        state = self._state(tags_applied=["milestone/industrial-age"])
        tv.write_gap_dashboard_json(state)
        data = json.loads((tmp_path / "world/gap_dashboard.json").read_text())
        assert "milestone/industrial-age" in data["milestones_achieved"]

    def test_at_risk_entity_included(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        # Build Nature Reserve by writing an entity file at the expected path
        cat_path = tmp_path / "world/entities/buildings"
        (cat_path / "_index.json").write_text(
            json.dumps({"next_seq": 2, "count": 1, "entities": ["bld-001"]}))
        (cat_path / "bld-001.json").write_text(
            json.dumps({"id": "bld-001", "name": "Nature Reserve"}))
        # green_policy at 30 — Nature Reserve removal threshold is 25, so 30 <= 25+4 = 29? No.
        # Let's find the actual remove value from WORLD_GENERATION_RULES
        remove_val = next(
            (r[4] for r in tv.WORLD_GENERATION_RULES if r[3] == "Nature Reserve"), 0)
        state = self._state(green_policy=remove_val + 2)
        tv.write_gap_dashboard_json(state)
        data = json.loads((tmp_path / "world/gap_dashboard.json").read_text())
        at_risk_names = [e["name"] for e in data["at_risk"]]
        assert "Nature Reserve" in at_risk_names


# ===========================================================================
# generate_leaderboard
# ===========================================================================

class TestGenerateLeaderboard:
    def _make_world(self, tmp_path):
        (tmp_path / "world").mkdir()

    def test_leaderboard_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        citizens = {
            "alice": {"total_votes": 10, "total_proposals": 2, "achievements": ["first_vote"]},
            "bob":   {"total_votes": 3,  "total_proposals": 0, "achievements": []},
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        tv.generate_leaderboard()
        content = (tmp_path / "world/LEADERBOARD.md").read_text(encoding="utf-8")
        assert "@alice" in content
        assert "@bob" in content
        assert "10" in content

    def test_representative_marked(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        citizens = {
            "alice": {"total_votes": 10, "total_proposals": 0, "achievements": ["representative"]},
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        (tmp_path / "world/representatives.json").write_text(
            json.dumps({"representatives": ["alice"]}))
        tv.generate_leaderboard()
        content = (tmp_path / "world/LEADERBOARD.md").read_text(encoding="utf-8")
        assert "[REP]" in content

    def test_achievements_in_leaderboard(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._make_world(tmp_path)
        citizens = {
            "alice": {"total_votes": 5, "total_proposals": 1,
                      "achievements": ["first_vote", "legislator"]},
        }
        (tmp_path / "world/citizens.json").write_text(json.dumps(citizens))
        tv.generate_leaderboard()
        content = (tmp_path / "world/LEADERBOARD.md").read_text(encoding="utf-8")
        assert "first_vote" in content
        assert "legislator" in content
