import json
import sys
from pathlib import Path

import pytest

from tests.helpers import tv  # noqa: F401 — side-effect: env setup

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import update_readme


# ---------------------------------------------------------------------------
# _badge_val
# ---------------------------------------------------------------------------

class TestBadgeVal:
    def test_spaces_become_underscores(self):
        assert update_readme._badge_val("Iron Age") == "Iron_Age"

    def test_special_chars_percent_encoded(self):
        val = update_readme._badge_val("100%")
        assert "%" in val
        assert " " not in val

    def test_integer_converts_to_string(self):
        assert update_readme._badge_val(42) == "42"

    def test_plain_string_unchanged(self):
        assert update_readme._badge_val("Modern") == "Modern"


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

class TestMain:
    def _write_state(self, tmp_path, **overrides):
        state = {
            "era": "Founding Era",
            "population": 500,
            "treasury": 150,
            "stability": 78,
            "laws_count": 3,
            "pollution": 10,
        }
        state.update(overrides)
        (tmp_path / "world").mkdir(exist_ok=True)
        (tmp_path / "world/state.json").write_text(json.dumps(state), encoding="utf-8")

    def _write_readme(self, tmp_path, content=None):
        if content is None:
            content = (
                "# Title\n\n"
                "<!-- WORLD-STATE-START -->\nold badges here\n<!-- WORLD-STATE-END -->\n\n"
                "## More content\n"
            )
        (tmp_path / "README.md").write_text(content, encoding="utf-8")

    def test_replaces_badge_block(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_state(tmp_path)
        self._write_readme(tmp_path)
        update_readme.main()
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "old badges here" not in readme
        assert "<!-- WORLD-STATE-START -->" in readme
        assert "<!-- WORLD-STATE-END -->" in readme

    def test_era_in_badge(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_state(tmp_path, era="Iron Age")
        self._write_readme(tmp_path)
        update_readme.main()
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "Iron_Age" in readme

    def test_population_in_badge(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_state(tmp_path, population=1234)
        self._write_readme(tmp_path)
        update_readme.main()
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "1234" in readme

    def test_treasury_in_badge(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_state(tmp_path, treasury=999)
        self._write_readme(tmp_path)
        update_readme.main()
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "999" in readme

    def test_laws_count_in_badge(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_state(tmp_path, laws_count=7)
        self._write_readme(tmp_path)
        update_readme.main()
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "7" in readme

    def test_pollution_in_badge(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_state(tmp_path, pollution=25)
        self._write_readme(tmp_path)
        update_readme.main()
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "25" in readme

    def test_missing_fields_use_defaults(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "world").mkdir()
        (tmp_path / "world/state.json").write_text("{}", encoding="utf-8")
        self._write_readme(tmp_path)
        update_readme.main()
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "Founding_Era" in readme

    def test_content_outside_block_preserved(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_state(tmp_path)
        self._write_readme(tmp_path)
        update_readme.main()
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "# Title" in readme
        assert "## More content" in readme

    def test_no_badge_block_leaves_readme_unchanged_structure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._write_state(tmp_path)
        content = "# No badges here\n\nJust regular content.\n"
        self._write_readme(tmp_path, content=content)
        update_readme.main()
        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "# No badges here" in readme

    def test_prints_update_message(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        self._write_state(tmp_path, era="Space Age", population=2000, treasury=500)
        self._write_readme(tmp_path)
        update_readme.main()
        out = capsys.readouterr().out
        assert "README badges updated" in out
        assert "Space_Age" in out
