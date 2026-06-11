#!/usr/bin/env python3
"""Rewrite the world-state badge block in README.md from current state.json."""
import json
import re
from pathlib import Path
from urllib.parse import quote


def _badge_val(s: str) -> str:
    """Encode a value for shields.io badge URLs (space→_, other special chars → %XX)."""
    return quote(str(s).replace(" ", "_"), safe="")


def main():
    state = json.loads(Path("world/state.json").read_text(encoding="utf-8"))

    era    = _badge_val(state.get("era", "Founding Era"))
    pop    = str(state.get("population", 0))
    trs    = str(state.get("treasury", 0))
    stb    = str(state.get("stability", 0))
    laws   = str(state.get("laws_count", 0))
    pol    = str(state.get("pollution", 0))

    badges = "\n".join([
        f"![Era](https://img.shields.io/badge/Era-{era}-e3b341?style=flat-square&logo=github)",
        f"![Population](https://img.shields.io/badge/Population-{pop}-3fb950?style=flat-square)",
        f"![Treasury](https://img.shields.io/badge/Treasury-{trs}_GC-388bfd?style=flat-square)",
        f"![Stability](https://img.shields.io/badge/Stability-{stb}%2F100-bc8cff?style=flat-square)",
        f"![Pollution](https://img.shields.io/badge/Pollution-{pol}%2F100-f85149?style=flat-square)",
        f"![Laws](https://img.shields.io/badge/Laws-{laws}_enacted-8b949e?style=flat-square)",
    ])

    readme = Path("README.md").read_text(encoding="utf-8")
    new_readme = re.sub(
        r"<!-- WORLD-STATE-START -->.*?<!-- WORLD-STATE-END -->",
        f"<!-- WORLD-STATE-START -->\n{badges}\n<!-- WORLD-STATE-END -->",
        readme,
        flags=re.DOTALL,
    )
    Path("README.md").write_text(new_readme, encoding="utf-8")
    print(f"README badges updated: era={era} pop={pop} treasury={trs}")


if __name__ == "__main__":
    main()
