import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tests.helpers import _import_validate


def _make_world(tmp_path, treasury=200):
    (tmp_path / "world").mkdir(exist_ok=True)
    state = {"treasury": treasury, "currency": "GC", "laws_count": 0,
             "education": 50, "industry": 50, "welfare": 50,
             "green_policy": 50, "defense": 50}
    (tmp_path / "world/state.json").write_text(json.dumps(state))
    for cat in ("buildings", "districts", "institutions", "sectors"):
        p = tmp_path / "world/entities" / cat
        p.mkdir(parents=True, exist_ok=True)
        (p / "_index.json").write_text(
            json.dumps({"next_seq": 1, "count": 0, "entities": []}))


def _llm_valid(vp):
    mc = MagicMock()
    mc.chat.completions.create.return_value.choices[0].message.content = (
        '{"valid": true, "reason": ""}')
    vp.OpenAI = MagicMock(return_value=mc)


def _llm_invalid(vp, reason="bad"):
    mc = MagicMock()
    mc.chat.completions.create.return_value.choices[0].message.content = (
        f'{{"valid": false, "reason": "{reason}"}}')
    vp.OpenAI = MagicMock(return_value=mc)


_GOOD_DESC = "## Description\n\nThis proposal has thirty or more characters here.\n\n"
_DECL_BODY = _GOOD_DESC + "## Effect\n\n```yaml\ntype: declaration\n```\n"
_POLICY_BODY = (
    _GOOD_DESC +
    "## Effect\n\n```yaml\ntype: policy\nchanges:\n  education: 10\n```\n"
)


# ===========================================================================
# check_cooldown_for_proposal — standalone (validate_proposal version)
# ===========================================================================

class TestValidateCooldownVP:
    def _vp(self, tmp_path):
        return _import_validate(tmp_path)

    def test_no_file_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 5}})
        assert ok

    def test_non_policy_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal({"type": "declaration"})
        assert ok

    def test_none_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(None)
        assert ok

    def test_corrupted_json_returns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/proposal_cooldowns.json").write_text("INVALID")
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 5}})
        assert ok

    def test_active_cooldown_blocks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": today}))
        ok, reason = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 5}})
        assert not ok
        assert "education" in reason

    def test_malformed_date_skipped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": "not-a-date"}))
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 5}})
        assert ok

    def test_metric_not_in_cooldowns_ok(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"defense": "2026-01-01"}))
        ok, _ = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 5}})
        assert ok

    def test_active_cooldown_dict_format_blocks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": {"last_date": today, "streak": 1}}))
        ok, reason = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 5}})
        assert not ok
        assert "education" in reason

    def test_active_cooldown_dict_format_streak_blocks(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": {"last_date": today, "streak": 3}}))
        ok, reason = self._vp(tmp_path).check_cooldown_for_proposal(
            {"type": "policy", "changes": {"education": 5}})
        assert not ok
        assert "education" in reason


# ===========================================================================
# load_world_context
# ===========================================================================

class TestLoadWorldContext:
    def _vp(self, tmp_path):
        return _import_validate(tmp_path)

    def test_missing_state_returns_empty_dict(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        for cat in ("buildings", "districts", "institutions", "sectors"):
            p = tmp_path / "world/entities" / cat
            p.mkdir(parents=True)
            (p / "_index.json").write_text(
                json.dumps({"next_seq": 1, "count": 0, "entities": []}))
        vp = self._vp(tmp_path)
        ctx = vp.load_world_context()
        assert ctx["state"] == {}

    def test_entity_read_error_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        (tmp_path / "world/entities/buildings/_index.json").write_text("INVALID")
        vp = self._vp(tmp_path)
        ctx = vp.load_world_context()
        assert ctx["entities"]["buildings"] == []

    def test_returns_entity_names(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        (tmp_path / "world/entities/buildings/_index.json").write_text(
            json.dumps({"next_seq": 2, "count": 1, "entities": ["bld-001"]}))
        (tmp_path / "world/entities/buildings/bld-001.json").write_text(
            json.dumps({"name": "Town Hall"}))
        vp = self._vp(tmp_path)
        ctx = vp.load_world_context()
        assert any("Town Hall" in n for n in ctx["entities"]["buildings"])


# ===========================================================================
# validate() — every fail() branch + success paths
# ===========================================================================

class TestValidateFunction:
    def _vp(self, tmp_path):
        return _import_validate(tmp_path)

    def _run(self, vp, title, body):
        vp.ISSUE_TITLE = title
        vp.ISSUE_BODY = body
        with patch("subprocess.run"):
            vp.validate()

    def _run_fail(self, vp, title, body):
        vp.ISSUE_TITLE = title
        vp.ISSUE_BODY = body
        with patch("subprocess.run"), pytest.raises(SystemExit):
            vp.validate()

    # --- title check ---

    def test_title_not_proposal_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "Wrong Title", "")

    # --- description checks ---

    def test_missing_description_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test", "no ## Description section")

    def test_empty_description_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test", "## Description\n\n   \n\n")

    def test_short_description_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test", "## Description\n\nToo short.")

    # --- YAML parsing ---

    def test_invalid_yaml_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\n: [unclosed\n```\n")

    def test_effect_not_dict_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\n- list item\n```\n")

    def test_unknown_effect_type_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: banana\n```\n")

    # --- required fields ---

    def test_policy_requires_changes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: policy\n```\n")

    def test_evolve_requires_id(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: evolve\n"
                       "changes:\n  name: New\n```\n")

    def test_state_patch_requires_patch(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n```\n")

    # --- policy validation ---

    def test_policy_empty_changes_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: policy\n"
                       "changes: {}\n```\n")

    def test_policy_unknown_metric_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: policy\n"
                       "changes:\n  happiness: 10\n```\n")

    def test_policy_non_integer_delta_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: policy\n"
                       "changes:\n  education: lots\n```\n")

    def test_policy_delta_over_50_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: policy\n"
                       "changes:\n  education: 51\n```\n")

    # --- evolve validation ---

    def test_evolve_entity_not_found_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: evolve\n"
                       "id: bld-999\nchanges:\n  name: New\n```\n")

    def test_evolve_empty_changes_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        bld = tmp_path / "world/entities/buildings"
        (bld / "bld-001.json").write_text(json.dumps({"name": "School"}))
        (bld / "_index.json").write_text(
            json.dumps({"next_seq": 2, "count": 1, "entities": ["bld-001"]}))
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: evolve\n"
                       "id: bld-001\nchanges: {}\n```\n")

    def test_evolve_blocked_system_field_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        bld = tmp_path / "world/entities/buildings"
        (bld / "bld-001.json").write_text(json.dumps({"name": "School"}))
        (bld / "_index.json").write_text(
            json.dumps({"next_seq": 2, "count": 1, "entities": ["bld-001"]}))
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: evolve\n"
                       "id: bld-001\nchanges:\n  built_law: 42\n```\n")

    def test_evolve_non_scalar_value_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        bld = tmp_path / "world/entities/buildings"
        (bld / "bld-001.json").write_text(json.dumps({"name": "School"}))
        (bld / "_index.json").write_text(
            json.dumps({"next_seq": 2, "count": 1, "entities": ["bld-001"]}))
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: evolve\n"
                       "id: bld-001\nchanges:\n  description: [a, b]\n```\n")

    def test_evolve_valid_entity_passes_rule_check(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        bld = tmp_path / "world/entities/buildings"
        (bld / "bld-001.json").write_text(json.dumps({"name": "School"}))
        (bld / "_index.json").write_text(
            json.dumps({"next_seq": 2, "count": 1, "entities": ["bld-001"]}))
        vp = self._vp(tmp_path)
        _llm_valid(vp)
        calls = []
        vp.ISSUE_TITLE = "[PROPOSAL] Rename School"
        vp.ISSUE_BODY = (
            _GOOD_DESC +
            "## Effect\n\n```yaml\ntype: evolve\n"
            "id: bld-001\nchanges:\n  name: Grand School\n```\n"
        )
        with patch("subprocess.run", side_effect=lambda *a, **k: calls.append(a[0])):
            vp.validate()
        assert any("proposal" in str(c) for c in calls)

    # --- state_patch validation ---

    def test_state_patch_empty_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch: {}\n```\n")

    def test_state_patch_disallowed_key_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  cheat_code: 9999\n```\n")

    def test_state_patch_metric_out_of_range_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  education: 150\n```\n")

    def test_state_patch_metric_non_int_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  education: abc\n```\n")

    def test_state_patch_population_out_of_range_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  population: -1\n```\n")

    def test_state_patch_population_non_int_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  population: many\n```\n")

    def test_state_patch_treasury_out_of_range_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  treasury: 200000\n```\n")

    def test_state_patch_treasury_non_int_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  treasury: rich\n```\n")

    def test_state_patch_currency_empty_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  currency: ''\n```\n")

    def test_state_patch_currency_too_long_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  currency: ThisNameIsFarTooLongToBeValidHere\n```\n")

    def test_state_patch_currency_not_string_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  currency: 42\n```\n")

    def test_state_patch_founded_date_not_string_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  founded_date: 12345\n```\n")

    def test_state_patch_founded_date_invalid_iso_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        self._run_fail(vp, "[PROPOSAL] Test",
                       _GOOD_DESC + "## Effect\n\n```yaml\ntype: state_patch\n"
                       "patch:\n  founded_date: not-a-date\n```\n")

    def test_state_patch_valid_passes_rule_check(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        _llm_valid(vp)
        vp.ISSUE_TITLE = "[PROPOSAL] Replenish Treasury"
        vp.ISSUE_BODY = (
            _GOOD_DESC +
            "## Effect\n\n```yaml\ntype: state_patch\n"
            "patch:\n  treasury: 500\n```\n"
        )
        calls = []
        with patch("subprocess.run", side_effect=lambda *a, **k: calls.append(a[0])):
            vp.validate()
        assert any("proposal" in str(c) for c in calls)

    # --- LLM validation ---

    def test_llm_invalid_result_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        _llm_invalid(vp, "Incoherent proposal")
        vp.ISSUE_TITLE = "[PROPOSAL] Test"
        vp.ISSUE_BODY = _DECL_BODY
        with patch("subprocess.run"), pytest.raises(SystemExit):
            vp.validate()

    def test_llm_bad_json_defaults_to_valid(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        mc = MagicMock()
        mc.chat.completions.create.return_value.choices[0].message.content = "not json"
        vp.OpenAI = MagicMock(return_value=mc)
        vp.ISSUE_TITLE = "[PROPOSAL] Test"
        vp.ISSUE_BODY = _DECL_BODY
        calls = []
        with patch("subprocess.run", side_effect=lambda *a, **k: calls.append(a[0])):
            vp.validate()
        assert any("proposal" in str(c) for c in calls)

    # --- cooldown after LLM ---

    def test_cooldown_active_after_llm_fails(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        (tmp_path / "world/proposal_cooldowns.json").write_text(
            json.dumps({"education": today}))
        vp = self._vp(tmp_path)
        _llm_valid(vp)
        vp.ISSUE_TITLE = "[PROPOSAL] Test"
        vp.ISSUE_BODY = _POLICY_BODY
        with patch("subprocess.run"), pytest.raises(SystemExit):
            vp.validate()

    # --- treasury notice ---

    def test_treasury_notice_sufficient(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path, treasury=200)
        vp = self._vp(tmp_path)
        _llm_valid(vp)
        vp.ISSUE_TITLE = "[PROPOSAL] Test"
        vp.ISSUE_BODY = _POLICY_BODY
        calls = []
        with patch("subprocess.run", side_effect=lambda *a, **k: calls.append(a[0])):
            vp.validate()
        assert any("Treasury check passed" in str(c) for c in calls)

    def test_treasury_notice_insufficient(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path, treasury=0)
        vp = self._vp(tmp_path)
        _llm_valid(vp)
        vp.ISSUE_TITLE = "[PROPOSAL] Test"
        vp.ISSUE_BODY = _POLICY_BODY
        calls = []
        with patch("subprocess.run", side_effect=lambda *a, **k: calls.append(a[0])):
            vp.validate()
        assert any("insufficient" in str(c) for c in calls)

    def test_valid_declaration_adds_label(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        _llm_valid(vp)
        vp.ISSUE_TITLE = "[PROPOSAL] Build a great park for the citizens"
        vp.ISSUE_BODY = _DECL_BODY
        calls = []
        with patch("subprocess.run", side_effect=lambda *a, **k: calls.append(a[0])):
            vp.validate()
        assert any("--add-label" in str(c) and "proposal" in str(c) for c in calls)

    def test_treasury_notice_exception_handled(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path, treasury=200)
        vp = self._vp(tmp_path)
        _llm_valid(vp)
        vp.ISSUE_TITLE = "[PROPOSAL] Test"
        vp.ISSUE_BODY = _POLICY_BODY

        def fake_run(cmd, *args, **kwargs):
            if "comment" in cmd:
                raise RuntimeError("gh error")

        with patch("subprocess.run", side_effect=fake_run):
            vp.validate()

    def test_no_effect_block_passes_declaration_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_world(tmp_path)
        vp = self._vp(tmp_path)
        _llm_valid(vp)
        vp.ISSUE_TITLE = "[PROPOSAL] A simple declaration"
        vp.ISSUE_BODY = _GOOD_DESC  # no ## Effect section at all
        calls = []
        with patch("subprocess.run", side_effect=lambda *a, **k: calls.append(a[0])):
            vp.validate()
        assert any("proposal" in str(c) for c in calls)
