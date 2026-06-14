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

import scripts.engine.world as _world_mod


# ===========================================================================
# determine_era
# ===========================================================================

class TestDetermineEra:
    def test_founding_era_default(self):
        state = {**BASE_STATE, "industry": 20, "education": 30, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Founding Era"

    def test_industrial_era(self):
        state = {**BASE_STATE, "industry": 65, "education": 55, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Industrial Era"

    def test_industrial_era_boundary_exact(self):
        state = {**BASE_STATE, "industry": 60, "education": 50, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Industrial Era"

    def test_industrial_era_just_below(self):
        state = {**BASE_STATE, "industry": 59, "education": 50, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Founding Era"

    def test_modern_era(self):
        state = {**BASE_STATE,
                 "education": 65, "industry": 65, "welfare": 65,
                 "green_policy": 65, "defense": 65, "pollution": 0, "stability": 79}
        assert tv.determine_era(state) == "Modern Era"

    def test_golden_age(self):
        state = {**BASE_STATE,
                 "education": 82, "industry": 81, "welfare": 83,
                 "green_policy": 80, "defense": 80, "pollution": 0, "stability": 82}
        assert tv.determine_era(state) == "Golden Age"

    def test_golden_age_needs_all_above_80(self):
        state = {**BASE_STATE,
                 "education": 82, "industry": 79, "welfare": 83,
                 "green_policy": 80, "defense": 80, "pollution": 0, "stability": 82}
        # industry = 79 → not Golden Age; all >= 65 → Modern Era
        assert tv.determine_era(state) == "Modern Era"

    def test_crisis_age_pollution(self):
        state = {**BASE_STATE, "pollution": 75, "stability": 79}
        assert tv.determine_era(state) == "Crisis Age"

    def test_crisis_age_stability(self):
        state = {**BASE_STATE, "pollution": 0, "stability": 25}
        assert tv.determine_era(state) == "Crisis Age"

    def test_crisis_overrides_golden(self):
        # Even if all policy metrics are high, pollution crisis wins
        state = {**BASE_STATE,
                 "education": 90, "industry": 90, "welfare": 90,
                 "green_policy": 90, "defense": 90,
                 "pollution": 80, "stability": 90}
        assert tv.determine_era(state) == "Crisis Age"


# ===========================================================================
# Idle economy (via world_autonomous_tick)
# ===========================================================================

class TestIdleEconomy:
    def _run_tick(self, state: dict) -> dict:
        """Run one tick against a temporary state file; return new state."""
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with patch.object(_engine_world, "read_state", return_value=dict(state)), \
                 patch.object(_engine_world, "write_state") as mock_write, \
                 patch.object(_engine_world.random, "uniform", return_value=0.0):
                tv.world_autonomous_tick()
                if mock_write.called:
                    return mock_write.call_args[0][0]
                return state

    def test_industrial_income(self):
        state = {**BASE_STATE, "industry": 30}  # 30//10 = 3 GC
        new = self._run_tick(state)
        assert new["treasury"] == BASE_STATE["treasury"] + 3 + (new["population"] // 500)

    def test_industrial_income_max(self):
        state = {**BASE_STATE, "industry": 80}  # 80//10 = 8 GC
        new = self._run_tick(state)
        pop_income = new["population"] // 500
        assert new["treasury"] == BASE_STATE["treasury"] + 8 + pop_income

    def test_population_income(self):
        state = {**BASE_STATE, "population": 1000}  # 1000//500 = 2 GC
        new = self._run_tick(state)
        ind_income = state["industry"] // 10
        assert new["treasury"] == state["treasury"] + ind_income + (new["population"] // 500)

    def test_welfare_population_bonus(self):
        # High welfare grows population (births > deaths, no migration penalty).
        base = {**BASE_STATE, "welfare": 70}
        new = self._run_tick(base)
        assert new["population"] > base["population"]

    def test_pollution_population_penalty(self):
        state = {**BASE_STATE, "welfare": 70, "industry": 80, "green_policy": 0}
        # ind - grn = 80 -> pol_delta = +1 -> new_pol = 1 (still low)
        new = self._run_tick(state)
        assert new["pollution"] == 1

    def test_high_pollution_population_penalty(self):
        # Extreme pollution combined with neutral welfare causes population decline
        # (pop>70 death bonus dominates birth rate).
        state = {**BASE_STATE, "pollution": 70, "welfare": 50,
                 "industry": 80, "green_policy": 0}
        new = self._run_tick(state)
        assert new["population"] < state["population"]

    def test_era_recomputed_in_tick(self):
        state = {**BASE_STATE, "industry": 65, "education": 55, "pollution": 0, "stability": 79}
        new = self._run_tick(state)
        assert new["era"] == "Industrial Era"


# ===========================================================================
# apply_event_effects
# ===========================================================================

class TestApplyEventEffects:
    def _apply(self, state: dict, effects: dict, key: str = "immediate_effects") -> dict:
        event = {"immediate_effects": effects, "default_consequence": effects,
                 "response_consequence": effects}
        captured = {}
        with patch.object(_engine_world, "read_state", return_value=dict(state)), \
             patch.object(_engine_world, "write_state", side_effect=lambda s: captured.update(s)):
            tv.apply_event_effects(event, key)
        return captured

    def test_policy_metric_clamped_to_100(self):
        new = self._apply(BASE_STATE, {"education": 50})
        assert new["education"] == min(100, BASE_STATE["education"] + 50)

    def test_policy_metric_clamped_to_0(self):
        state = {**BASE_STATE, "welfare": 3}
        new = self._apply(state, {"welfare": -10})
        assert new["welfare"] == 0

    def test_treasury_no_lower_bound(self):
        # treasury uses max(0,...) so it can't go negative
        state = {**BASE_STATE, "treasury": 50}
        new = self._apply(state, {"treasury": -200})
        assert new["treasury"] == 0

    def test_population_delta(self):
        new = self._apply(BASE_STATE, {"population": 500})
        assert new["population"] == BASE_STATE["population"] + 500

    def test_stability_clamped(self):
        state = {**BASE_STATE, "stability": 95}
        new = self._apply(state, {"stability": 10})
        assert new["stability"] == 100


# ===========================================================================
# apply_effect — state_patch allowlist (C1 defense-in-depth)
# ===========================================================================

class TestApplyEffectStatePatch:
    def test_allowed_key_applied(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        effect = {"type": "state_patch", "patch": {"treasury": 500}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 500

    def test_blocked_key_silently_skipped(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        original = {**BASE_STATE, "laws_count": 5}
        (tmp_path / "world/state.json").write_text(json.dumps(original))
        effect = {"type": "state_patch", "patch": {"laws_count": 0, "treasury": 300}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["laws_count"] == 5   # blocked key unchanged
        assert state["treasury"] == 300   # allowed key applied
        assert "BLOCKED" in capsys.readouterr().out

    def test_numeric_value_clamped_to_0_100(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        effect = {"type": "state_patch", "patch": {"education": 999}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["education"] == 100  # clamped

    def test_null_value_skipped(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        original = {**BASE_STATE}
        (tmp_path / "world/state.json").write_text(json.dumps(original))
        effect = {"type": "state_patch", "patch": {"education": None}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["education"] == BASE_STATE["education"]  # unchanged
        assert "BLOCKED" in capsys.readouterr().out

    def test_treasury_capped_at_100000(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps({**BASE_STATE}))
        effect = {"type": "state_patch", "patch": {"treasury": 9_999_999}}
        tv.apply_effect(effect, None)
        state = json.loads((tmp_path / "world/state.json").read_text())
        assert state["treasury"] == 100_000


# ===========================================================================
# apply_effect evolve — _EVOLVE_BLOCKED defense-in-depth
# ===========================================================================

class TestApplyEffectEvolveBlocked:
    def _make_entity(self, tmp_path, category="buildings", name="bld-001"):
        (tmp_path / "world" / "entities" / category).mkdir(parents=True)
        entity = {
            "id": name, "name": "Test Building",
            "built_law": 3, "built_at": "2026-01-01T00:00:00Z",
            "auto_trigger": "education>=25",
        }
        (tmp_path / f"world/entities/{category}/{name}.json").write_text(json.dumps(entity))

    def test_blocked_field_not_overwritten(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        self._make_entity(tmp_path)
        effect = {"type": "evolve", "id": "bld-001",
                  "changes": {"built_law": 999, "capacity": 500}}
        tv.apply_effect(effect, 10)
        entity = json.loads((tmp_path / "world/entities/buildings/bld-001.json").read_text())
        assert entity["built_law"] == 3    # blocked — unchanged
        assert entity["capacity"] == 500   # allowed — applied

    def test_allowed_field_updated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text(json.dumps(BASE_STATE))
        self._make_entity(tmp_path)
        effect = {"type": "evolve", "id": "bld-001",
                  "changes": {"level": 2, "description": "Upgraded"}}
        tv.apply_effect(effect, 10)
        entity = json.loads((tmp_path / "world/entities/buildings/bld-001.json").read_text())
        assert entity["level"] == 2
        assert entity["description"] == "Upgraded"
        assert entity["last_evolved_law"] == 10  # always set


# ===========================================================================
# world_autonomous_tick — treasury cap
# ===========================================================================

class TestAutonomousTickTreasuryCap:
    def test_treasury_never_exceeds_100000(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        state = {**BASE_STATE, "treasury": 99_990, "industry": 80, "population": 1000}
        (tmp_path / "world/state.json").write_text(json.dumps(state))
        with patch.object(_engine_world, "write_state") as ws, \
             patch.object(_engine_world, "run", return_value=""):
            tv.world_autonomous_tick()
            written = ws.call_args[0][0]
        assert written["treasury"] <= 100_000


# ===========================================================================
# world_autonomous_tick — timing guard (next_tick_at)
# ===========================================================================

class TestAutonomousTickTimingGuard:
    def _state_with_next_tick(self, tmp_path, delta_seconds):
        from datetime import timedelta
        (tmp_path / "world").mkdir(exist_ok=True)
        next_tick = (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        state = {**BASE_STATE, "next_tick_at": next_tick}
        (tmp_path / "world/state.json").write_text(json.dumps(state))
        return state

    def test_skips_when_before_next_tick_at(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._state_with_next_tick(tmp_path, delta_seconds=3600)  # 1 hour from now
        with patch.object(_engine_world, "SKIP_TIMING", False), \
             patch.object(_engine_world, "write_state") as ws:
            result = tv.world_autonomous_tick()
        assert result is False
        ws.assert_not_called()

    def test_runs_when_past_next_tick_at(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._state_with_next_tick(tmp_path, delta_seconds=-3600)  # 1 hour ago
        with patch.object(_engine_world, "SKIP_TIMING", False), \
             patch.object(_engine_world, "write_state") as ws, \
             patch.object(_engine_world, "run", return_value=""):
            result = tv.world_autonomous_tick()
        assert result is True
        ws.assert_called_once()

    def test_skip_timing_overrides_guard(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._state_with_next_tick(tmp_path, delta_seconds=3600)  # 1 hour from now
        with patch.object(_engine_world, "SKIP_TIMING", True), \
             patch.object(_engine_world, "write_state") as ws, \
             patch.object(_engine_world, "run", return_value=""):
            result = tv.world_autonomous_tick()
        assert result is True
        ws.assert_called_once()


# ===========================================================================
# entity_exists_by_name, auto_create_entity, auto_remove_entity
# ===========================================================================

class TestEntityExistsByName:
    def test_finds_existing_entity(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Public School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        assert tv.entity_exists_by_name("buildings", "Public School") == "bld-001"

    def test_case_insensitive_match(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Public School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        assert tv.entity_exists_by_name("buildings", "public school") == "bld-001"
        assert tv.entity_exists_by_name("buildings", "PUBLIC SCHOOL") == "bld-001"

    def test_returns_none_when_not_found(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        assert tv.entity_exists_by_name("buildings", "Nonexistent") is None

    def test_missing_entity_file_skipped(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cat_path = tmp_path / "world" / "entities" / "buildings"
        cat_path.mkdir(parents=True)
        index = {"next_seq": 2, "count": 1, "entities": ["bld-001"]}
        (cat_path / "_index.json").write_text(json.dumps(index))
        # bld-001.json intentionally absent
        assert tv.entity_exists_by_name("buildings", "Anything") is None


class TestAutoCreateEntity:
    def test_creates_entity_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        eid = tv.auto_create_entity("buildings", "Test School", 5, "education>=25")
        assert (tmp_path / "world" / "entities" / "buildings" / f"{eid}.json").exists()

    def test_entity_has_correct_fields(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        eid = tv.auto_create_entity("buildings", "Test School", 5, "education>=25")
        data = json.loads(
            (tmp_path / "world/entities/buildings" / f"{eid}.json").read_text())
        assert data["name"] == "Test School"
        assert data["built_law"] == 5
        assert data["auto_trigger"] == "education>=25"

    def test_index_updated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        eid = tv.auto_create_entity("buildings", "Test School", 5, "education>=25")
        idx = json.loads(
            (tmp_path / "world/entities/buildings/_index.json").read_text())
        assert idx["count"] == 1
        assert eid in idx["entities"]

    def test_sequential_ids(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "districts")
        id1 = tv.auto_create_entity("districts", "Park A", 1, "green_policy>=35")
        id2 = tv.auto_create_entity("districts", "Park B", 2, "green_policy>=35")
        assert id1 != id2
        idx = json.loads(
            (tmp_path / "world/entities/districts/_index.json").read_text())
        assert idx["count"] == 2


class TestAutoRemoveEntity:
    def test_entity_file_deleted(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Old School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        tv.auto_remove_entity("buildings", "bld-001", 10, "education dropped")
        assert not (tmp_path / "world/entities/buildings/bld-001.json").exists()

    def test_archived_to_world_archive(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Old School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        tv.auto_remove_entity("buildings", "bld-001", 10, "education dropped")
        assert (tmp_path / "world/archive/bld-001.json").exists()

    def test_index_count_decremented(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Old School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        tv.auto_remove_entity("buildings", "bld-001", 10, "education dropped")
        idx = json.loads(
            (tmp_path / "world/entities/buildings/_index.json").read_text())
        assert idx["count"] == 0
        assert "bld-001" not in idx["entities"]

    def test_nonexistent_entity_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        tv.auto_remove_entity("buildings", "bld-999", 5, "reason")

    def test_demolished_fields_in_archive(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "Old School", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "education>=25"},
        ])
        tv.auto_remove_entity("buildings", "bld-001", 10, "education dropped")
        archived = json.loads((tmp_path / "world/archive/bld-001.json").read_text())
        assert archived["demolished_law"] == 10
        assert "demolished_at" in archived
        assert archived["auto_reason"] == "education dropped"


# ===========================================================================
# run_world_engine — entity creation/removal from policy metrics
# ===========================================================================

class TestRunWorldEngine:
    def _setup_world(self, tmp_path, state_override=None):
        (tmp_path / "world").mkdir()
        state = {**BASE_STATE, **(state_override or {})}
        (tmp_path / "world/state.json").write_text(json.dumps(state))
        for cat in ("buildings", "districts", "institutions", "sectors"):
            _make_category(tmp_path, cat)

    def test_entity_created_when_metric_above_threshold(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, {"education": 55})
        changes = tv.run_world_engine(5)
        assert any("National University" in c for c in changes)
        assert tv.entity_exists_by_name("institutions", "National University") is not None

    def test_no_duplicate_entity_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, {"education": 55})
        tv.run_world_engine(5)
        changes2 = tv.run_world_engine(6)
        assert not any("National University" in c for c in changes2)
        idx = json.loads(
            (tmp_path / "world/entities/institutions/_index.json").read_text())
        assert idx["count"] == 1

    def test_entity_removed_when_metric_drops(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, {"education": 55})
        tv.run_world_engine(5)
        (tmp_path / "world/state.json").write_text(
            json.dumps({**BASE_STATE, "education": 40}))
        changes = tv.run_world_engine(6)
        assert any("National University" in c for c in changes)
        assert tv.entity_exists_by_name("institutions", "National University") is None

    def test_no_change_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # All metrics below all appear-thresholds → nothing created
        self._setup_world(tmp_path, {
            "education": 10, "industry": 10, "welfare": 10,
            "green_policy": 10, "defense": 10, "pollution": 0,
        })
        changes = tv.run_world_engine(1)
        assert changes == []

    def test_pollution_entity_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_world(tmp_path, {"pollution": 65})
        changes = tv.run_world_engine(7)
        assert any("Smog Zone" in c for c in changes)


# ===========================================================================
# slugify — special character handling
# ===========================================================================

class TestSlugify:
    def test_lowercase_conversion(self):
        assert tv.slugify("Industrial Era") == "industrial-era"

    def test_colon_replaced(self):
        assert tv.slugify("Crisis: Pollution") == "crisis-pollution"

    def test_multiple_specials_collapsed(self):
        assert tv.slugify("Hello   World!!") == "hello-world"

    def test_leading_trailing_stripped(self):
        assert tv.slugify("--hello--") == "hello"

    def test_already_valid(self):
        assert tv.slugify("founding-era") == "founding-era"

    def test_numbers_preserved(self):
        assert tv.slugify("Era 2050") == "era-2050"

    def test_unicode_letters_stripped(self):
        result = tv.slugify("Café Era")
        assert "caf" in result
        assert " " not in result


# ===========================================================================
# check_threshold_tags — milestone crossing detection
# ===========================================================================

class TestCheckThresholdTags:
    def _state(self, **overrides):
        return {**BASE_STATE, "tags_applied": [], **overrides}

    def test_above_crossing_triggers(self):
        before = self._state(welfare=59)
        after  = self._state(welfare=61)
        tags = tv.check_threshold_tags(before, after)
        assert any(t[0] == "milestone/welfare-state" for t in tags)

    def test_above_crossing_updates_tags_applied(self):
        before = self._state(welfare=59)
        after  = self._state(welfare=61)
        tv.check_threshold_tags(before, after)
        assert "milestone/welfare-state" in after["tags_applied"]

    def test_already_applied_not_duplicated(self):
        before = self._state(welfare=59)
        after  = self._state(welfare=61, tags_applied=["milestone/welfare-state"])
        tags = tv.check_threshold_tags(before, after)
        assert not any(t[0] == "milestone/welfare-state" for t in tags)

    def test_below_crossing_triggers(self):
        before = self._state(pollution=25)
        after  = self._state(pollution=15)
        tags = tv.check_threshold_tags(before, after)
        assert any(t[0] == "recovery/air-cleaned" for t in tags)

    def test_no_crossing_no_tag(self):
        before = self._state(welfare=50)
        after  = self._state(welfare=55)
        tags = tv.check_threshold_tags(before, after)
        assert not any(t[0] == "milestone/welfare-state" for t in tags)

    def test_exact_threshold_not_crossed_from_below(self):
        # bv <= threshold < av requires av > threshold; av == threshold doesn't fire
        before = self._state(welfare=59)
        after  = self._state(welfare=60)
        tags = tv.check_threshold_tags(before, after)
        assert not any(t[0] == "milestone/welfare-state" for t in tags)

    def test_exact_threshold_crossed_above(self):
        before = self._state(welfare=60)
        after  = self._state(welfare=61)
        tags = tv.check_threshold_tags(before, after)
        assert any(t[0] == "milestone/welfare-state" for t in tags)

    def test_multiple_tags_fired_in_one_call(self):
        before = self._state(welfare=59, education=49)
        after  = self._state(welfare=61, education=51)
        tags = tv.check_threshold_tags(before, after)
        tag_names = [t[0] for t in tags]
        assert "milestone/welfare-state" in tag_names
        assert "milestone/educated-society" in tag_names

    def test_missing_field_skipped(self):
        before = {}
        after  = {"tags_applied": []}
        tags = tv.check_threshold_tags(before, after)
        assert tags == []


# ===========================================================================
# pollution_level
# ===========================================================================

class TestPollutionLevel:
    def test_explicit_pollution_field_used(self):
        # When "pollution" key exists it is used directly
        state = {**BASE_STATE, "pollution": 42}
        assert tv.pollution_level(state) == 42

    def test_pollution_clamped_to_0(self):
        state = {**BASE_STATE, "pollution": -5}
        assert tv.pollution_level(state) == 0

    def test_pollution_clamped_to_100(self):
        state = {**BASE_STATE, "pollution": 150}
        assert tv.pollution_level(state) == 100

    def test_computed_from_industry_minus_green_policy(self):
        # No "pollution" key; derived as industry - green_policy (clamped)
        state = {"industry": 50, "green_policy": 20}
        assert tv.pollution_level(state) == 30

    def test_computed_result_clamped_low(self):
        state = {"industry": 5, "green_policy": 80}
        assert tv.pollution_level(state) == 0

    def test_zero_when_both_absent(self):
        # industry and green_policy both default to 0
        assert tv.pollution_level({}) == 0


# ===========================================================================
# env_bg_color
# ===========================================================================

class TestEnvBgColor:
    def test_returns_hex_string(self):
        color = tv.env_bg_color(0)
        assert color.startswith("#")
        assert len(color) == 7

    def test_zero_pollution_clean_color(self):
        # At pollution=0, color should equal the clean baseline: #161b22
        assert tv.env_bg_color(0) == "#161b22"

    def test_max_pollution_dirty_color(self):
        # At pollution=100, color should equal the dirty baseline: #1e0e05
        assert tv.env_bg_color(100) == "#1e0e05"

    def test_midpoint_is_between_bounds(self):
        clean = tv.env_bg_color(0)
        dirty = tv.env_bg_color(100)
        mid   = tv.env_bg_color(50)
        # Mid color must differ from both extremes
        assert mid != clean
        assert mid != dirty

    def test_pollution_clamped_below_zero(self):
        # Values below 0 treated as 0
        assert tv.env_bg_color(-10) == tv.env_bg_color(0)

    def test_pollution_clamped_above_100(self):
        # Values above 100 treated as 100
        assert tv.env_bg_color(200) == tv.env_bg_color(100)

    def test_monotonic_red_increases_with_pollution(self):
        # Red channel increases as pollution rises (dirty is redder)
        low_r  = int(tv.env_bg_color(0)[1:3],   16)
        high_r = int(tv.env_bg_color(100)[1:3],  16)
        assert high_r > low_r


# ===========================================================================
# next_entity_id
# ===========================================================================

class TestNextEntityId:
    def test_returns_formatted_id_and_seq(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        eid, seq = tv.next_entity_id("buildings")
        # _make_category starts next_seq at 1 for an empty category
        assert eid == "bld-001"
        assert seq == 1

    def test_prefix_for_districts(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "districts")
        eid, seq = tv.next_entity_id("districts")
        assert eid.startswith("dst-")

    def test_prefix_for_institutions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "institutions")
        eid, _ = tv.next_entity_id("institutions")
        assert eid.startswith("ins-")

    def test_prefix_for_sectors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "sectors")
        eid, _ = tv.next_entity_id("sectors")
        assert eid.startswith("sec-")

    def test_seq_reflects_existing_entries(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        # Place two entities; next_seq should be 3
        _make_category(tmp_path, "buildings", [
            {"id": "bld-001", "name": "A", "built_law": 1,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "x"},
            {"id": "bld-002", "name": "B", "built_law": 2,
             "built_at": "2026-01-01T00:00:00Z", "auto_trigger": "x"},
        ])
        eid, seq = tv.next_entity_id("buildings")
        assert seq == 3
        assert eid == "bld-003"

    def test_does_not_modify_index(self, tmp_path, monkeypatch):
        # next_entity_id is read-only — should not increment next_seq
        monkeypatch.chdir(tmp_path)
        _make_category(tmp_path, "buildings")
        tv.next_entity_id("buildings")
        idx = json.loads(
            (tmp_path / "world/entities/buildings/_index.json").read_text())
        assert idx["next_seq"] == 1  # unchanged


# ===========================================================================
# apply_tags
# ===========================================================================

class TestApplyTags:
    def _state(self, **kwargs):
        return {**BASE_STATE, "tags_applied": [], **kwargs}

    def test_era_transition_tag_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = self._state(era="Founding Era")
        after  = self._state(era="Industrial Era")
        with patch.object(_engine_world, "run") as mock_run:
            tv.apply_tags(None, before, after, 5, "Build Factory", [])
            # run(["git", "tag", "-a", tag_name, "-m", msg]) — tag_name is at index 3
            tag_names = [call.args[0][3] for call in mock_run.call_args_list]
            assert any(t.startswith("era/industrial-era") for t in tag_names)

    def test_no_era_tag_when_era_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = self._state(era="Founding Era")
        after  = self._state(era="Founding Era")
        with patch.object(_engine_world, "run") as mock_run:
            tv.apply_tags(None, before, after, 1, "Nothing", [])
            assert mock_run.call_count == 0

    def test_threshold_tags_are_applied(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = self._state(era="Founding Era")
        after  = self._state(era="Founding Era")
        threshold_tags = [("milestone/educated-society", "World milestone: education crossed 50")]
        with patch.object(_engine_world, "run") as mock_run:
            tv.apply_tags(None, before, after, 3, "Edu boost", threshold_tags)
            tag_names = [call.args[0][3] for call in mock_run.call_args_list]
            assert "milestone/educated-society" in tag_names

    def test_declaration_tag_with_slash_applied(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = self._state(era="Founding Era")
        after  = self._state(era="Founding Era")
        effect = {"type": "declaration", "tag": "constitution/freedom-of-speech"}
        with patch.object(_engine_world, "run") as mock_run:
            tv.apply_tags(effect, before, after, 7, "Free Speech", [])
            tag_names = [call.args[0][3] for call in mock_run.call_args_list]
            assert "constitution/freedom-of-speech" in tag_names

    def test_declaration_tag_without_slash_not_applied(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = self._state(era="Founding Era")
        after  = self._state(era="Founding Era")
        effect = {"type": "declaration", "tag": "no-slash-tag"}
        with patch.object(_engine_world, "run") as mock_run:
            tv.apply_tags(effect, before, after, 7, "Bad Tag", [])
            assert mock_run.call_count == 0

    def test_non_declaration_type_ignored(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = self._state(era="Founding Era")
        after  = self._state(era="Founding Era")
        effect = {"type": "policy", "tag": "some/tag"}
        with patch.object(_engine_world, "run") as mock_run:
            tv.apply_tags(effect, before, after, 7, "Policy", [])
            assert mock_run.call_count == 0

    def test_no_effect_data_does_not_crash(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        before = self._state(era="Founding Era")
        after  = self._state(era="Founding Era")
        with patch.object(_engine_world, "run") as mock_run:
            tv.apply_tags(None, before, after, 1, "Nothing", [])
            assert mock_run.call_count == 0


# ===========================================================================
# compute_next_tick_at
# ===========================================================================

class TestComputeNextTickAt:
    def test_advances_by_two_hours_at_top_of_hour(self):
        now = datetime(2026, 6, 14, 18, 0, 0, tzinfo=timezone.utc)
        assert tv.compute_next_tick_at(now) == "2026-06-14T20:00:00Z"

    def test_snaps_to_top_of_hour_when_now_is_mid_hour(self):
        now = datetime(2026, 6, 14, 18, 37, 42, tzinfo=timezone.utc)
        assert tv.compute_next_tick_at(now) == "2026-06-14T20:00:00Z"

    def test_consecutive_calls_two_hours_apart_produce_different_values(self):
        # Regression: the old 4-hour boundary calculation made consecutive 2h
        # cron runs collide on the same timestamp. Two adjacent runs must now
        # always differ.
        first  = tv.compute_next_tick_at(datetime(2026, 6, 14, 18, 0, tzinfo=timezone.utc))
        second = tv.compute_next_tick_at(datetime(2026, 6, 14, 20, 0, tzinfo=timezone.utc))
        third  = tv.compute_next_tick_at(datetime(2026, 6, 14, 22, 0, tzinfo=timezone.utc))
        assert first != second != third
        assert first == "2026-06-14T20:00:00Z"
        assert second == "2026-06-14T22:00:00Z"
        assert third == "2026-06-15T00:00:00Z"

    def test_crosses_day_boundary(self):
        now = datetime(2026, 6, 14, 23, 0, 0, tzinfo=timezone.utc)
        assert tv.compute_next_tick_at(now) == "2026-06-15T01:00:00Z"

    def test_crosses_month_boundary(self):
        now = datetime(2026, 6, 30, 23, 30, 0, tzinfo=timezone.utc)
        assert tv.compute_next_tick_at(now) == "2026-07-01T01:00:00Z"

    def test_crosses_year_boundary(self):
        now = datetime(2026, 12, 31, 23, 30, 0, tzinfo=timezone.utc)
        assert tv.compute_next_tick_at(now) == "2027-01-01T01:00:00Z"

    def test_naive_datetime_treated_as_utc(self):
        # Defensive: if a caller passes a naive datetime, treat it as UTC
        # rather than crashing or producing a localized timestamp.
        naive = datetime(2026, 6, 14, 18, 0, 0)
        assert tv.compute_next_tick_at(naive) == "2026-06-14T20:00:00Z"

    def test_aware_non_utc_input(self):
        # Caller passes a tz-aware datetime in a non-UTC zone — the snap
        # happens in the input's local zone, then format is UTC suffix.
        from datetime import timezone as _tz
        plus_eight = _tz(timedelta(hours=8))
        now = datetime(2026, 6, 14, 18, 0, 0, tzinfo=plus_eight)
        # 18:00+08 + 2h = 20:00+08; format() writes the local-clock string
        # with a literal "Z" suffix. We accept this as documented behavior.
        result = tv.compute_next_tick_at(now)
        assert result.startswith("2026-06-14T20:00:00")

    def test_midnight_input(self):
        now = datetime(2026, 6, 14, 0, 0, 0, tzinfo=timezone.utc)
        assert tv.compute_next_tick_at(now) == "2026-06-14T02:00:00Z"

    def test_format_is_iso_with_z_suffix(self):
        now = datetime(2026, 6, 14, 18, 0, 0, tzinfo=timezone.utc)
        result = tv.compute_next_tick_at(now)
        assert result.endswith("Z")
        assert "T" in result
        # Round-trip parseable
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert parsed.tzinfo is not None


# ===========================================================================
# compute_population_delta
# ===========================================================================

class _FixedRng:
    """Stand-in for random.Random returning a preset uniform() value."""
    def __init__(self, value=0.0):
        self._value = value
    def uniform(self, _a, _b):
        return self._value


class TestComputePopulationDelta:
    def _call(self, **overrides):
        defaults = dict(
            pop=1000, welfare=50, pollution=30,
            stability=50, defense=50, treasury=500,
            rng=_FixedRng(0.0),
        )
        defaults.update(overrides)
        return _engine_world.compute_population_delta(**defaults)

    # ── Growth conditions ──
    def test_high_welfare_grows_population(self):
        new = self._call(welfare=80, pollution=10, stability=80)
        assert new > 1000

    def test_high_treasury_boosts_birth_rate(self):
        low_treas  = self._call(treasury=500)
        high_treas = self._call(treasury=5000)
        assert high_treas > low_treas

    def test_high_stability_boosts_birth_rate(self):
        low_stb  = self._call(stability=50)
        high_stb = self._call(stability=80)
        assert high_stb > low_stb

    # ── Decline conditions ──
    def test_extreme_pollution_kills_population(self):
        # Welfare neutral (no birth bonus) but pollution > 70 dominates.
        new = self._call(pop=2000, welfare=50, pollution=80, stability=50)
        assert new < 2000

    def test_extreme_pollution_balanced_by_max_welfare(self):
        # Documented behavior: maxed welfare (+ stability + treasury) can offset
        # pollution=80 to roughly zero net change. This is intentional gameplay —
        # a wealthy, stable, well-cared-for society can weather pollution.
        new = self._call(pop=2000, welfare=80, pollution=80,
                         stability=80, treasury=5000)
        # Net effect within ±5% (no migration triggered here)
        assert 1900 <= new <= 2100

    def test_pollution_above_50_increases_deaths(self):
        mild = self._call(pop=2000, pollution=40)
        med  = self._call(pop=2000, pollution=55)
        assert med < mild

    def test_low_welfare_increases_deaths(self):
        ok   = self._call(welfare=40)
        bad  = self._call(welfare=20)
        assert bad < ok

    def test_low_stability_triggers_migration_out(self):
        new = self._call(pop=2000, welfare=60, stability=20)
        assert new < 2000

    def test_low_defense_triggers_migration_out(self):
        new = self._call(pop=2000, welfare=60, defense=20)
        assert new < 2000

    def test_compound_collapse(self):
        # Bad on every axis — strong negative pressure.
        new = self._call(pop=5000, welfare=20, pollution=80,
                         stability=20, defense=20, treasury=0)
        assert new < 5000

    # ── Floor ──
    def test_floor_prevents_extinction_under_collapse(self):
        new = self._call(pop=100, welfare=10, pollution=95,
                         stability=10, defense=10, treasury=0)
        assert new >= 100

    def test_floor_pulls_zero_population_up(self):
        # Defensive: state with pop=0 shouldn't stay at 0 forever.
        new = self._call(pop=0)
        assert new >= 100

    def test_floor_caps_negative_pop_input(self):
        # Defensive: corrupted state with negative pop is clamped at 0 then floored.
        new = self._call(pop=-500)
        assert new >= 100

    # ── Noise ──
    def test_noise_zero_keeps_result_deterministic(self):
        a = self._call(rng=_FixedRng(0.0))
        b = self._call(rng=_FixedRng(0.0))
        assert a == b

    def test_positive_noise_increases_population(self):
        baseline = self._call(rng=_FixedRng(0.0))
        with_pos = self._call(rng=_FixedRng(+0.02))
        assert with_pos > baseline

    def test_negative_noise_decreases_population(self):
        baseline = self._call(rng=_FixedRng(0.0))
        with_neg = self._call(rng=_FixedRng(-0.02))
        assert with_neg < baseline

    # ── Boundary inputs ──
    def test_zero_welfare_pollution_stability_defense(self):
        # All-zero conditions should not crash and should yield decline.
        new = self._call(pop=1000, welfare=0, pollution=0,
                         stability=0, defense=0, treasury=0)
        assert new >= 100  # floor still applies

    def test_max_metrics_grows_strongly(self):
        new = self._call(pop=1000, welfare=100, pollution=0,
                         stability=100, defense=100, treasury=10_000)
        assert new > 1000

    def test_default_rng_uses_random_module(self):
        # Smoke test: omitting `rng` doesn't crash and stays within bounds.
        new = _engine_world.compute_population_delta(
            pop=1000, welfare=50, pollution=30,
            stability=50, defense=50, treasury=500,
        )
        # ±20% absolute bound is more than generous given the rates.
        assert 100 <= new <= 1400

    def test_birth_rate_threshold_is_strict_inequality(self):
        # `welfare > 60` is strict. Welfare exactly 60 should NOT get the bonus.
        with_60 = self._call(welfare=60)
        with_61 = self._call(welfare=61)
        assert with_61 > with_60

    # ── Integration ──
    def test_integrates_with_world_autonomous_tick(self, tmp_path, monkeypatch):
        """world_autonomous_tick should produce decreased population when conditions are bad."""
        monkeypatch.chdir(tmp_path)
        Path("world").mkdir()
        state = {
            **BASE_STATE,
            "pollution": 80,
            "stability": 20,
            "defense":   20,
            "welfare":   20,
            "population": 5000,
            "industry":  60,
            "green_policy": 10,
            "treasury":  100,
            "next_tick_at": None,
        }
        Path("world/state.json").write_text(json.dumps(state), encoding="utf-8")
        with patch.object(_engine_world, "SKIP_TIMING", True), \
             patch.object(_engine_world.random, "uniform", return_value=0.0):
            tv.world_autonomous_tick()
        new_state = json.loads(Path("world/state.json").read_text(encoding="utf-8"))
        assert new_state["population"] < 5000


# ===========================================================================
# _count_missed_ticks
# ===========================================================================

class TestCountMissedTicks:

    def test_not_yet_due_returns_zero(self):
        from datetime import datetime, timezone, timedelta
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        orig = _world_mod.SKIP_TIMING
        try:
            _world_mod.SKIP_TIMING = False
            assert _world_mod._count_missed_ticks({"next_tick_at": future}) == 0
        finally:
            _world_mod.SKIP_TIMING = orig

    def test_just_overdue_returns_one(self):
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        orig = _world_mod.SKIP_TIMING
        try:
            _world_mod.SKIP_TIMING = False
            assert _world_mod._count_missed_ticks({"next_tick_at": past}) == 1
        finally:
            _world_mod.SKIP_TIMING = orig

    def test_two_intervals_overdue_returns_two(self):
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=2, minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        orig = _world_mod.SKIP_TIMING
        try:
            _world_mod.SKIP_TIMING = False
            assert _world_mod._count_missed_ticks({"next_tick_at": past}) == 2
        finally:
            _world_mod.SKIP_TIMING = orig

    def test_caps_at_six(self):
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        orig = _world_mod.SKIP_TIMING
        try:
            _world_mod.SKIP_TIMING = False
            assert _world_mod._count_missed_ticks({"next_tick_at": past}) == 6
        finally:
            _world_mod.SKIP_TIMING = orig

    def test_skip_timing_returns_one_regardless(self):
        orig = _world_mod.SKIP_TIMING
        try:
            _world_mod.SKIP_TIMING = True
            assert _world_mod._count_missed_ticks({"next_tick_at": "2020-01-01T00:00:00Z"}) == 1
        finally:
            _world_mod.SKIP_TIMING = orig

    def test_missing_next_tick_at_returns_one(self):
        orig = _world_mod.SKIP_TIMING
        try:
            _world_mod.SKIP_TIMING = False
            assert _world_mod._count_missed_ticks({}) == 1
        finally:
            _world_mod.SKIP_TIMING = orig

    def test_malformed_timestamp_returns_one(self):
        orig = _world_mod.SKIP_TIMING
        try:
            _world_mod.SKIP_TIMING = False
            assert _world_mod._count_missed_ticks({"next_tick_at": "not-a-date"}) == 1
        finally:
            _world_mod.SKIP_TIMING = orig


# ===========================================================================
# world_autonomous_tick — multi-tick catchup
# ===========================================================================

class TestMultiTickCatchup:

    def _base_state(self, next_tick_at):
        return {
            "industry": 20, "green_policy": 0, "welfare": 50,
            "defense": 50, "pollution": 10, "population": 1000,
            "stability": 60, "treasury": 0, "era": "Founding Era",
            "next_tick_at": next_tick_at,
        }

    def test_catchup_applies_two_ticks(self, monkeypatch):
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(hours=2, minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        base = self._base_state(past)

        write_calls = []
        monkeypatch.setattr(_world_mod, "SKIP_TIMING", False)
        monkeypatch.setattr(_world_mod, "read_state", lambda: {**base})
        monkeypatch.setattr(_world_mod, "write_state", lambda s: write_calls.append(dict(s)))
        monkeypatch.setattr(_world_mod, "determine_era", lambda s: "Founding Era")

        result = _world_mod.world_autonomous_tick()
        assert result is True
        assert len(write_calls) >= 2, f"Expected >=2 write_state calls for 2-tick catchup, got {len(write_calls)}"

    def test_single_tick_when_barely_overdue(self, monkeypatch):
        from datetime import datetime, timezone, timedelta
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        base = self._base_state(past)

        write_calls = []
        monkeypatch.setattr(_world_mod, "SKIP_TIMING", False)
        monkeypatch.setattr(_world_mod, "read_state", lambda: {**base})
        monkeypatch.setattr(_world_mod, "write_state", lambda s: write_calls.append(dict(s)))
        monkeypatch.setattr(_world_mod, "determine_era", lambda s: "Founding Era")

        result = _world_mod.world_autonomous_tick()
        assert result is True
        assert len(write_calls) == 1

    def test_population_larger_after_three_ticks(self, monkeypatch):
        from datetime import datetime, timezone, timedelta

        past_1 = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        past_3 = (datetime.now(timezone.utc) - timedelta(hours=4, minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

        def run_ticks(past_ts):
            base = {
                "industry": 0, "green_policy": 0, "welfare": 80,
                "defense": 50, "pollution": 10, "population": 1000,
                "stability": 60, "treasury": 2000, "era": "Founding Era",
                "next_tick_at": past_ts,
            }
            states = [base]
            written = []
            monkeypatch.setattr(_world_mod, "SKIP_TIMING", False)
            monkeypatch.setattr(_world_mod, "read_state", lambda: dict(states[-1]))
            def fake_write(s):
                written.append(dict(s))
                states.append(dict(s))
            monkeypatch.setattr(_world_mod, "write_state", fake_write)
            monkeypatch.setattr(_world_mod, "determine_era", lambda s: "Founding Era")
            _world_mod.world_autonomous_tick()
            return written[-1]["population"] if written else 1000

        # Use seeded random to make test deterministic
        import random
        orig_uniform = random.uniform
        monkeypatch.setattr(random, "uniform", lambda a, b: 0.0)
        pop_1 = run_ticks(past_1)
        pop_3 = run_ticks(past_3)
        monkeypatch.setattr(random, "uniform", orig_uniform)

        assert pop_3 >= pop_1, f"3-tick population {pop_3} should be >= 1-tick population {pop_1}"
