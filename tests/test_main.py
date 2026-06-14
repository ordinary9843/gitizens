import json
import sys
import types
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
# main() ordering guarantees — publish after push, per-proposal isolation
# ===========================================================================

class TestMainOrdering:
    def _setup_world(self, tmp_path):
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(
            json.dumps({**BASE_STATE, "laws_count": 0}), encoding="utf-8")
        (tmp_path / "world/stats.json").write_text("{}", encoding="utf-8")
        (tmp_path / "world/history.json").write_text("[]", encoding="utf-8")
        (tmp_path / "world/dispatches.json").write_text("[]", encoding="utf-8")
        (tmp_path / "world/active_event.json").write_text("{}", encoding="utf-8")
        (tmp_path / "world/pinned_comment_ids.json").write_text("{}", encoding="utf-8")
        for cat in ("buildings", "districts", "institutions", "sectors"):
            cat_path = tmp_path / "world/entities" / cat
            cat_path.mkdir(parents=True)
            (cat_path / "_index.json").write_text(
                json.dumps({"next_seq": 1, "count": 0, "entities": []}))
        (tmp_path / "world/laws").mkdir(parents=True)

    @pytest.fixture(autouse=True)
    def _inject_auto_propose(self, monkeypatch):
        mock_ap = types.ModuleType("auto_propose")
        mock_ap.should_generate = MagicMock(return_value=(False, False))
        mock_ap.generate_ai_proposal = MagicMock()
        mock_ap.generate_feedbacks = MagicMock()
        monkeypatch.setitem(sys.modules, "auto_propose", mock_ap)

    def _common_patches(self, **overrides):
        base = dict(
            _ensure_labels=MagicMock(),
            collect_star_income=MagicMock(),
            world_autonomous_tick=MagicMock(return_value=False),
            get_open_proposals=MagicMock(return_value=[]),
            get_ai_proposals=MagicMock(return_value=[]),
            get_feedbacks=MagicMock(return_value=[]),
            load_active_event=MagicMock(return_value={}),
            check_event_expiry=MagicMock(return_value=False),
            fire_random_event=MagicMock(return_value=None),
            save_dispatch=MagicMock(),
            append_history_snapshot=MagicMock(),
            generate_annals=MagicMock(),
            select_weekly_representatives=MagicMock(),
            generate_citizen_narrator=MagicMock(),
            run=MagicMock(return_value=""),
            read_state=MagicMock(side_effect=lambda: dict({**BASE_STATE, "laws_count": 0})),
            write_state=MagicMock(),
            read_stats=MagicMock(return_value={}),
            generate_world_md=MagicMock(),
            update_readme=MagicMock(),
            push_with_retry=MagicMock(return_value=True),
            publish_dispatch=MagicMock(),
        )
        base.update(overrides)
        return base

    def test_publish_skipped_when_push_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        patches = self._common_patches(push_with_retry=MagicMock(return_value=False))
        with patch.multiple(tv, **patches):
            with pytest.raises(SystemExit) as exc_info:
                tv.main()
        assert exc_info.value.code == 1
        patches["publish_dispatch"].assert_not_called()

    def test_publish_called_when_push_succeeds(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        patches = self._common_patches(push_with_retry=MagicMock(return_value=True))
        with patch.multiple(tv, **patches):
            tv.main()
        patches["publish_dispatch"].assert_called_once()

    def test_proposal_error_does_not_abort_remaining(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        processed = []

        def fake_process(issue):
            if issue["number"] == 1:
                raise RuntimeError("simulated proposal failure")
            processed.append(issue["number"])

        proposals = [
            {"number": 1, "title": "[PROPOSAL] Bad"},
            {"number": 2, "title": "[PROPOSAL] Good"},
        ]
        patches = self._common_patches(
            get_open_proposals=MagicMock(return_value=proposals),
            process_issue=MagicMock(side_effect=fake_process),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        assert 2 in processed

    def test_update_world_summary_called_every_tick(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        patches = self._common_patches(
            update_world_summary=MagicMock(return_value="Fresh summary"),
            write_state=MagicMock(),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        patches["update_world_summary"].assert_called_once()


# ===========================================================================
# _validate_state() — file existence and JSON integrity checks
# ===========================================================================

class TestValidateState:
    def test_valid_state_passes(self, tmp_path, monkeypatch):
        """A well-formed world/state.json must not raise."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(
            json.dumps(BASE_STATE), encoding="utf-8"
        )
        # Should complete without raising.
        tv._validate_state()

    def test_missing_state_raises_system_exit(self, tmp_path, monkeypatch):
        """Absence of world/state.json must raise SystemExit."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        # Do not create state.json.
        with pytest.raises(SystemExit):
            tv._validate_state()

    def test_corrupted_json_raises_system_exit(self, tmp_path, monkeypatch):
        """world/state.json containing invalid JSON must raise SystemExit."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(
            "{not valid json}", encoding="utf-8"
        )
        with pytest.raises(SystemExit):
            tv._validate_state()


# ===========================================================================
# TestMainBranches — missing coverage in tally_votes.py main()
# ===========================================================================

class TestMainBranches:
    def _setup_world(self, tmp_path):
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(
            json.dumps({**BASE_STATE, "laws_count": 0}), encoding="utf-8")
        (tmp_path / "world/stats.json").write_text("{}", encoding="utf-8")
        (tmp_path / "world/history.json").write_text("[]", encoding="utf-8")
        (tmp_path / "world/dispatches.json").write_text("[]", encoding="utf-8")
        (tmp_path / "world/active_event.json").write_text("{}", encoding="utf-8")
        (tmp_path / "world/pinned_comment_ids.json").write_text("{}", encoding="utf-8")
        for cat in ("buildings", "districts", "institutions", "sectors"):
            cat_path = tmp_path / "world/entities" / cat
            cat_path.mkdir(parents=True)
            (cat_path / "_index.json").write_text(
                json.dumps({"next_seq": 1, "count": 0, "entities": []}))
        (tmp_path / "world/laws").mkdir(parents=True)

    @pytest.fixture(autouse=True)
    def _inject_auto_propose(self, monkeypatch):
        mock_ap = types.ModuleType("auto_propose")
        mock_ap.should_generate = MagicMock(return_value=(False, False))
        mock_ap.generate_ai_proposal = MagicMock()
        mock_ap.generate_feedbacks = MagicMock()
        monkeypatch.setitem(sys.modules, "auto_propose", mock_ap)

    def _common_patches(self, **overrides):
        base = dict(
            _ensure_labels=MagicMock(),
            collect_star_income=MagicMock(),
            world_autonomous_tick=MagicMock(return_value=False),
            get_open_proposals=MagicMock(return_value=[]),
            get_ai_proposals=MagicMock(return_value=[]),
            get_feedbacks=MagicMock(return_value=[]),
            process_issue=MagicMock(),
            process_ai_proposal=MagicMock(),
            process_feedback=MagicMock(return_value=False),
            load_active_event=MagicMock(return_value={}),
            check_event_expiry=MagicMock(return_value=False),
            fire_random_event=MagicMock(return_value=None),
            apply_event_effects=MagicMock(),
            open_event_issue=MagicMock(return_value=99),
            save_active_event=MagicMock(),
            save_dispatch=MagicMock(),
            append_history_snapshot=MagicMock(),
            generate_annals=MagicMock(),
            select_weekly_representatives=MagicMock(),
            generate_leaderboard=MagicMock(),
            write_gap_dashboard_json=MagicMock(),
            save_proposals_json=MagicMock(),
            generate_citizen_narrator=MagicMock(),
            run=MagicMock(return_value=""),
            read_state=MagicMock(side_effect=lambda: dict({**BASE_STATE, "laws_count": 0})),
            write_state=MagicMock(),
            read_stats=MagicMock(return_value={}),
            generate_world_md=MagicMock(),
            update_readme=MagicMock(),
            update_world_summary=MagicMock(return_value="summary"),
            compute_next_tick_at=MagicMock(return_value="2099-01-01T00:00:00Z"),
            push_with_retry=MagicMock(return_value=True),
            publish_dispatch=MagicMock(),
        )
        base.update(overrides)
        return base

    def test_proposal_law_tracks_proposer(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        laws_count = [0]

        def fake_read_state():
            return {**BASE_STATE, "laws_count": laws_count[0]}

        def fake_process_issue(_proposal):
            laws_count[0] += 1

        proposal = {"number": 1, "title": "[PROPOSAL] Test",
                    "author": {"login": "alice"}}
        patches = self._common_patches(
            get_open_proposals=MagicMock(return_value=[proposal]),
            process_issue=MagicMock(side_effect=fake_process_issue),
            read_state=MagicMock(side_effect=fake_read_state),
        )
        save_dispatch_mock = patches["save_dispatch"]
        with patch.multiple(tv, **patches):
            tv.main()
        call_kwargs = save_dispatch_mock.call_args
        proposers = call_kwargs[1].get("proposers") if call_kwargs[1] else None
        if proposers is None and call_kwargs[0]:
            proposers = call_kwargs[0][-1]
        assert proposers == ["alice"]

    def test_ai_proposal_loop_runs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        ai_proposal = {"number": 10, "title": "[AI-PROPOSAL] Boost Schools"}
        patches = self._common_patches(
            get_ai_proposals=MagicMock(return_value=[ai_proposal]),
            process_ai_proposal=MagicMock(),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        patches["process_ai_proposal"].assert_called_once_with(ai_proposal)

    def test_ai_proposal_law_increments_count(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        laws_count = [0]

        def fake_read_state():
            return {**BASE_STATE, "laws_count": laws_count[0]}

        def fake_process_ai(_proposal):
            laws_count[0] += 1

        ai_proposal = {"number": 10, "title": "[AI-PROPOSAL] Boost"}
        patches = self._common_patches(
            get_ai_proposals=MagicMock(return_value=[ai_proposal]),
            process_ai_proposal=MagicMock(side_effect=fake_process_ai),
            read_state=MagicMock(side_effect=fake_read_state),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        patches["process_ai_proposal"].assert_called_once()

    def test_ai_proposal_exception_handled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        ai_proposal = {"number": 11, "title": "[AI-PROPOSAL] Failing"}
        patches = self._common_patches(
            get_ai_proposals=MagicMock(return_value=[ai_proposal]),
            process_ai_proposal=MagicMock(side_effect=RuntimeError("ai fail")),
            push_with_retry=MagicMock(return_value=True),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        patches["publish_dispatch"].assert_called_once()

    def test_feedbacks_counted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        feedback = {"number": 20, "title": "[FEEDBACK] Noise"}
        patches = self._common_patches(
            get_feedbacks=MagicMock(return_value=[feedback]),
            process_feedback=MagicMock(return_value=True),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        patches["process_feedback"].assert_called_once_with(feedback)

    def test_new_event_fired_when_no_active(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        new_event = {"title": "Great Flood", "rarity": "rare",
                     "immediate_effects": [], "id": "ev-001"}
        patches = self._common_patches(
            load_active_event=MagicMock(return_value=None),
            fire_random_event=MagicMock(return_value=new_event),
            open_event_issue=MagicMock(return_value=55),
            save_active_event=MagicMock(),
            apply_event_effects=MagicMock(),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        patches["save_active_event"].assert_called_once()
        patches["apply_event_effects"].assert_called_once()
        patches["open_event_issue"].assert_called_once_with(new_event)

    def test_active_event_title_from_existing_event(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        existing_event = {"title": "Drought", "rarity": "common"}
        patches = self._common_patches(
            load_active_event=MagicMock(return_value=existing_event),
            fire_random_event=MagicMock(return_value=None),
        )
        save_dispatch_mock = patches["save_dispatch"]
        with patch.multiple(tv, **patches):
            tv.main()
        args = save_dispatch_mock.call_args[0]
        assert "Drought" in args

    def test_auto_propose_called_when_should_generate(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        mock_ap = types.ModuleType("auto_propose")
        mock_ap.should_generate = MagicMock(return_value=(True, True))
        mock_ap.generate_ai_proposal = MagicMock()
        mock_ap.generate_feedbacks = MagicMock()
        monkeypatch.setitem(sys.modules, "auto_propose", mock_ap)
        patches = self._common_patches()
        with patch.multiple(tv, **patches):
            tv.main()
        mock_ap.generate_ai_proposal.assert_called_once()
        mock_ap.generate_feedbacks.assert_called_once()

    def test_auto_propose_exception_handled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        mock_ap = types.ModuleType("auto_propose")
        mock_ap.should_generate = MagicMock(return_value=(True, False))
        mock_ap.generate_ai_proposal = MagicMock(side_effect=RuntimeError("api down"))
        mock_ap.generate_feedbacks = MagicMock()
        monkeypatch.setitem(sys.modules, "auto_propose", mock_ap)
        patches = self._common_patches()
        with patch.multiple(tv, **patches):
            tv.main()
        patches["publish_dispatch"].assert_called_once()

    def test_history_json_decode_error_handled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        (tmp_path / "world/history.json").write_text("NOT_JSON", encoding="utf-8")
        patches = self._common_patches()
        generate_annals_mock = patches["generate_annals"]
        with patch.multiple(tv, **patches):
            tv.main()
        generate_annals_mock.assert_called_once_with([])

    def test_commit_msg_event_resolved_with_title(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        committed = []

        def fake_run(cmd):
            if "--porcelain" in cmd:
                return "M world/state.json"
            if "commit" in cmd:
                committed.append(cmd[-1])
            return ""

        patches = self._common_patches(
            run=MagicMock(side_effect=fake_run),
            load_active_event=MagicMock(return_value={"title": "The Storm"}),
            check_event_expiry=MagicMock(return_value=True),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        assert committed and "[EVENT] resolved:" in committed[0]
        assert "The Storm" in committed[0]

    def test_commit_msg_event_resolved_no_title(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        committed = []

        def fake_run(cmd):
            if "--porcelain" in cmd:
                return "M world/state.json"
            if "commit" in cmd:
                committed.append(cmd[-1])
            return ""

        patches = self._common_patches(
            run=MagicMock(side_effect=fake_run),
            load_active_event=MagicMock(return_value={}),
            check_event_expiry=MagicMock(return_value=True),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        assert committed and committed[0] == "[EVENT] event resolved"

    def test_commit_msg_tick_changed(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        committed = []

        def fake_run(cmd):
            if "--porcelain" in cmd:
                return "M world/state.json"
            if "commit" in cmd:
                committed.append(cmd[-1])
            return ""

        patches = self._common_patches(
            run=MagicMock(side_effect=fake_run),
            world_autonomous_tick=MagicMock(return_value=True),
            check_event_expiry=MagicMock(return_value=False),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        assert committed and committed[0] == "[WORLD] autonomous tick"

    def test_commit_msg_state_update(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path)
        committed = []

        def fake_run(cmd):
            if "--porcelain" in cmd:
                return "M world/state.json"
            if "commit" in cmd:
                committed.append(cmd[-1])
            return ""

        patches = self._common_patches(
            run=MagicMock(side_effect=fake_run),
            world_autonomous_tick=MagicMock(return_value=False),
            check_event_expiry=MagicMock(return_value=False),
        )
        with patch.multiple(tv, **patches):
            tv.main()
        assert committed and committed[0] == "[WORLD] state update"
