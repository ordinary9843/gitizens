import json
import math
from pathlib import Path

from .constants import CATEGORIES
from .state import read_json, read_state
from .world import pollution_level, env_bg_color


def svg_radar(cx: float, cy: float, r: float,
              vals: list[float], colors: list[str], labels: list[str],
              font_size: int = 7) -> str:
    n = len(vals)
    angles = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]
    parts: list[str] = []

    for frac in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(
            f"{cx + r * frac * math.cos(a):.1f},{cy + r * frac * math.sin(a):.1f}"
            for a in angles
        )
        parts.append(f'<polygon points="{pts}" fill="none" stroke="#30363d" stroke-width="0.5"/>')

    for a in angles:
        ax, ay = cx + r * math.cos(a), cy + r * math.sin(a)
        parts.append(
            f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{ax:.1f}" y2="{ay:.1f}"'
            f' stroke="#30363d" stroke-width="0.5"/>'
        )

    data_pts = " ".join(
        f"{cx + r * (v / 100) * math.cos(a):.1f},{cy + r * (v / 100) * math.sin(a):.1f}"
        for v, a in zip(vals, angles)
    )
    parts.append(
        f'<polygon points="{data_pts}" fill="rgba(56,139,253,0.14)" stroke="#388bfd" stroke-width="1.2"/>'
    )

    for v, a, c in zip(vals, angles, colors):
        dx, dy = cx + r * (v / 100) * math.cos(a), cy + r * (v / 100) * math.sin(a)
        parts.append(f'<circle cx="{dx:.1f}" cy="{dy:.1f}" r="2.2" fill="{c}"/>')

    for lbl, a in zip(labels, angles):
        lx = cx + (r + 9) * math.cos(a)
        ly = cy + (r + 9) * math.sin(a)
        if math.cos(a) > 0.3:
            anchor = "start"
        elif math.cos(a) < -0.3:
            anchor = "end"
        else:
            anchor = "middle"
        dy_attr = ' dy="0.35em"' if abs(math.sin(a)) < 0.3 else (
            ' dy="0.7em"' if math.sin(a) > 0 else ' dy="-0.2em"'
        )
        parts.append(
            f'<text x="{lx:.1f}" y="{ly:.1f}"{dy_attr} fill="#484f58"'
            f' font-family="monospace" font-size="{font_size}"'
            f' text-anchor="{anchor}">{lbl}</text>'
        )

    return "\n  ".join(parts)


def generate_dashboard_svg(stats: dict, date: str):
    state = read_state()
    era = state.get("era", "Founding Era")
    laws = state.get("laws_count", 0)
    treasury = state.get("treasury")

    from datetime import datetime, timezone
    founded_date = state.get("founded_date")
    if founded_date:
        delta = (datetime.now(timezone.utc).date() -
                 datetime.fromisoformat(founded_date).date())
        day_str = f"Day {delta.days + 1} of {era}"
    else:
        day_str = era

    passed = stats.get("proposals_passed", 0)
    rejected = stats.get("proposals_rejected", 0)
    total = passed + rejected
    pass_rate = round(passed / max(total, 1) * 100)

    total_entities = 0
    for cat, _ in CATEGORIES:
        try:
            total_entities += read_json(Path(f"world/entities/{cat}/_index.json")).get("count", 0)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    max_bar = 580
    scale = max_bar / max(passed, rejected, 1)
    passed_w  = max(int(passed  * scale), 4 if passed  > 0 else 0)
    rejected_w = max(int(rejected * scale), 4 if rejected > 0 else 0)

    treasury_str   = f"{treasury:,}" if isinstance(treasury, (int, float)) else "—"
    treasury_color = "#e3b341" if treasury is not None else "#484f58"

    edu = state.get("education", 0)
    ind = state.get("industry", 0)
    wel = state.get("welfare", 0)
    grn = state.get("green_policy", 0)
    dfn = state.get("defense", 0)
    pol = pollution_level(state)
    pop = state.get("population", 0)
    stb = state.get("stability", 0)

    def bar_w(val, max_w=270):
        return max(int(val / 100 * max_w), 2 if val > 0 else 0)

    def bar_w_sm(val):
        return bar_w(val, max_w=130)

    def mc(val):
        if val >= 60: return "#3fb950"
        if val >= 30: return "#e3b341"
        return "#484f58"

    pol_color = "#f85149" if pol >= 60 else "#e3b341" if pol >= 30 else "#3fb950"
    pop_str   = f"{pop:,}" if pop else "—"
    stb_color = "#3fb950" if stb >= 60 else "#e3b341" if stb >= 40 else "#f85149"

    radar = svg_radar(
        660, 183, 50,
        [edu, ind, wel, grn, dfn],
        ["#388bfd", "#bc8cff", "#3fb950", "#2dd4bf", "#f0883e"],
        ["EDU", "IND", "WEL", "GRN", "DEF"],
    )

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="760" height="370">
  <rect width="760" height="370" rx="8" fill="#161b22"/>

  <text x="24" y="40" fill="#c9d1d9" font-family="monospace" font-size="20" font-weight="bold">GITIZENS</text>
  <text x="24" y="58" fill="#8b949e" font-family="monospace" font-size="12">{day_str}</text>

  <line x1="24" y1="70" x2="736" y2="70" stroke="#30363d" stroke-width="1"/>

  <text x="24"  y="90"  fill="#484f58" font-family="monospace" font-size="10">LAWS ENACTED</text>
  <text x="24"  y="116" fill="#c9d1d9" font-family="monospace" font-size="26" font-weight="bold">{laws}</text>

  <text x="210" y="90"  fill="#484f58" font-family="monospace" font-size="10">STRUCTURES</text>
  <text x="210" y="116" fill="#c9d1d9" font-family="monospace" font-size="26" font-weight="bold">{total_entities}</text>

  <text x="396" y="90"  fill="#484f58" font-family="monospace" font-size="10">TREASURY</text>
  <text x="396" y="116" fill="{treasury_color}" font-family="monospace" font-size="26" font-weight="bold">{treasury_str}</text>

  <text x="582" y="90"  fill="#484f58" font-family="monospace" font-size="10">PASS RATE</text>
  <text x="582" y="116" fill="#3fb950" font-family="monospace" font-size="26" font-weight="bold">{pass_rate}%</text>

  <line x1="24" y1="132" x2="736" y2="132" stroke="#30363d" stroke-width="1"/>

  <text x="24"  y="150" fill="#484f58" font-family="monospace" font-size="10">POLICY METRICS</text>
  <text x="700" y="150" fill="{pol_color}" font-family="monospace" font-size="10" text-anchor="end">POLLUTION {pol}/100</text>

  <text x="24"  y="168" fill="#8b949e" font-family="monospace" font-size="10">EDU</text>
  <rect x="56"  y="158" width="{bar_w(edu)}" height="12" rx="2" fill="{mc(edu)}"/>
  <text x="{56 + bar_w(edu) + 4}" y="168" fill="{mc(edu)}" font-family="monospace" font-size="10">{edu}</text>

  <text x="24"  y="186" fill="#8b949e" font-family="monospace" font-size="10">IND</text>
  <rect x="56"  y="176" width="{bar_w(ind)}" height="12" rx="2" fill="{mc(ind)}"/>
  <text x="{56 + bar_w(ind) + 4}" y="186" fill="{mc(ind)}" font-family="monospace" font-size="10">{ind}</text>

  <text x="24"  y="204" fill="#8b949e" font-family="monospace" font-size="10">WEL</text>
  <rect x="56"  y="194" width="{bar_w(wel)}" height="12" rx="2" fill="{mc(wel)}"/>
  <text x="{56 + bar_w(wel) + 4}" y="204" fill="{mc(wel)}" font-family="monospace" font-size="10">{wel}</text>

  <text x="400" y="168" fill="#8b949e" font-family="monospace" font-size="10">GRN</text>
  <rect x="432" y="158" width="{bar_w_sm(grn)}" height="12" rx="2" fill="{mc(grn)}"/>
  <text x="{432 + bar_w_sm(grn) + 4}" y="168" fill="{mc(grn)}" font-family="monospace" font-size="10">{grn}</text>

  <text x="400" y="186" fill="#8b949e" font-family="monospace" font-size="10">DEF</text>
  <rect x="432" y="176" width="{bar_w_sm(dfn)}" height="12" rx="2" fill="{mc(dfn)}"/>
  <text x="{432 + bar_w_sm(dfn) + 4}" y="186" fill="{mc(dfn)}" font-family="monospace" font-size="10">{dfn}</text>

  {radar}

  <line x1="24" y1="218" x2="736" y2="218" stroke="#30363d" stroke-width="1"/>

  <text x="24" y="238" fill="#8b949e" font-family="monospace" font-size="11">Passed  </text>
  <rect x="100" y="225" width="{passed_w}" height="20" rx="3" fill="#3fb950"/>
  <text x="{passed_w + 108}" y="240" fill="#3fb950" font-family="monospace" font-size="11">{passed}</text>

  <text x="24" y="274" fill="#8b949e" font-family="monospace" font-size="11">Rejected</text>
  <rect x="100" y="261" width="{rejected_w}" height="20" rx="3" fill="#f85149"/>
  <text x="{rejected_w + 108}" y="276" fill="#f85149" font-family="monospace" font-size="11">{rejected}</text>

  <line x1="24" y1="296" x2="736" y2="296" stroke="#30363d" stroke-width="1"/>

  <text x="24"  y="314" fill="#484f58" font-family="monospace" font-size="10">POPULATION</text>
  <text x="280" y="314" fill="#484f58" font-family="monospace" font-size="10">POLLUTION</text>
  <text x="536" y="314" fill="#484f58" font-family="monospace" font-size="10">STABILITY</text>

  <text x="24"  y="334" fill="#c9d1d9" font-family="monospace" font-size="18" font-weight="bold">{pop_str}</text>
  <text x="280" y="334" fill="{pol_color}" font-family="monospace" font-size="18" font-weight="bold">{pol}/100</text>
  <text x="536" y="334" fill="{stb_color}" font-family="monospace" font-size="18" font-weight="bold">{stb}/100</text>

  <text x="24" y="358" fill="#484f58" font-family="monospace" font-size="10">Total proposals: {total} | Updated: {date}</text>
</svg>"""
    Path("world/stats.svg").write_text(svg, encoding="utf-8")
