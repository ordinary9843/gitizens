"""
Shared test helpers and fixtures for the Gitizens test suite.
"""
import json
import sys
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import helpers — patch file I/O so tests don't touch real world/ files
# ---------------------------------------------------------------------------

# Minimal state used as a base for most tests
BASE_STATE = {
    "era": "Founding Era",
    "laws_count": 8,
    "treasury": 200,
    "education": 60,
    "industry": 35,
    "welfare": 70,
    "green_policy": 70,
    "defense": 35,
    "population": 1000,
    "pollution": 0,
    "stability": 79,
}


def _import_module():
    """Import tally_votes with env vars stubbed out."""
    import os
    os.environ.setdefault("GITHUB_TOKEN", "test-token")
    os.environ.setdefault("GITHUB_REPOSITORY", "test/repo")
    # Stub out openai so import doesn't fail without real credentials
    sys.modules.setdefault("openai", MagicMock())
    sys.modules.setdefault("yaml", __import__("yaml"))
    import scripts.tally_votes as tv
    return tv


tv = _import_module()

# Engine submodule references for correct patch targeting after sys.path is set
import engine.gh        as _engine_gh
import engine.world     as _engine_world
import engine.events    as _engine_events
import engine.chronicle as _engine_chronicle
import engine.content   as _engine_content
import engine.proposals as _engine_proposals


def _make_category(tmp_path, category: str, entities: list | None = None):
    cat_path = tmp_path / "world" / "entities" / category
    cat_path.mkdir(parents=True)
    entities = entities or []
    index = {"next_seq": len(entities) + 1, "count": len(entities),
             "entities": [e["id"] for e in entities]}
    (cat_path / "_index.json").write_text(json.dumps(index))
    for e in entities:
        (cat_path / f"{e['id']}.json").write_text(json.dumps(e))
    return cat_path


def _import_validate(tmp_path):
    import importlib
    import os as _os
    for k, v in [("ISSUE_NUMBER", "1"), ("ISSUE_TITLE", "[PROPOSAL] Test"),
                 ("ISSUE_BODY", ""), ("GITHUB_TOKEN", "test-token"),
                 ("GITHUB_REPOSITORY", "test/repo")]:
        _os.environ.setdefault(k, v)
        if not _os.environ.get(k):
            _os.environ[k] = v
    sys.modules.pop("scripts.validate_proposal", None)
    import scripts.validate_proposal as vp
    return vp
