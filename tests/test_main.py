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
