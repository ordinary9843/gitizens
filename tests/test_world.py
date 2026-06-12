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
                 patch.object(_engine_world, "write_state") as mock_write:
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
        base = {**BASE_STATE, "welfare": 70}  # >60 triggers +100 extra
        new = self._run_tick(base)
        # base pop delta: 50 (welfare>=40) + 100 (welfare>60) = 150
        assert new["population"] == base["population"] + 150

    def test_pollution_population_penalty(self):
        state = {**BASE_STATE, "welfare": 70, "industry": 80, "green_policy": 0}
        # ind - grn = 80 → pol_delta = +1 → new_pol = 1
        # pop_delta = 50+100=150, no pollution penalty since new_pol < 70
        new = self._run_tick(state)
        assert new["pollution"] == 1

    def test_high_pollution_population_penalty(self):
        state = {**BASE_STATE, "pollution": 70, "welfare": 70, "industry": 80, "green_policy": 0}
        new = self._run_tick(state)
        # new_pol = 71 >= 70 → pop_delta -= 50; so 150-50=100
        assert new["population"] == state["population"] + 100

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
