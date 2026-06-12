import json
from datetime import datetime, timezone
from pathlib import Path

from .constants import WORLD_GENERATION_RULES, THRESHOLD_TAGS
from .gh import run, gh_json, REPO
from .state import read_json, read_state, write_state, read_stats, write_stats
from .svg import generate_dashboard_svg
from .content import client, upsert_bot_comment


def append_history(law_number: int | None, title: str, issue_number: int,
                   for_votes: int, against_votes: int, passed: bool, date: str):
    path = Path("history/INDEX.md")
    try:
        content = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        print(f"  [WARN] history/INDEX.md not found — skipping history append")
        return
    issue_link = f"[#{issue_number}](https://github.com/{REPO}/issues/{issue_number})"
    if passed:
        law_link = f"[law-{law_number:03d}](../world/laws/law-{law_number:03d}.md)"
        row = f"| {law_number} | {law_link} | {issue_link} {title} | {for_votes}+1 {against_votes}-1 | {date} |"
    else:
        row = f"| - | *(rejected)* | {issue_link} {title} | {for_votes}+1 {against_votes}-1 | {date} |"
    path.write_text(content + row + "\n", encoding="utf-8")


def update_laws_index(law_number: int, title: str, issue_number: int,
                      issue_url: str, era: str, date: str):
    path = Path("world/laws_index.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (json.JSONDecodeError, OSError):
        data = []
    data.append({
        "number": law_number,
        "title": title,
        "issue_number": issue_number,
        "issue_url": issue_url,
        "enacted_date": date,
        "era": era,
    })
    if len(data) > 20:
        data = data[-20:]
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _load_entity_names() -> set[str]:
    names: set[str] = set()
    for cat in ("buildings", "districts", "institutions", "sectors"):
        idx_path = Path(f"world/entities/{cat}/_index.json")
        if not idx_path.exists():
            continue
        try:
            idx = read_json(idx_path)
            for eid in idx.get("entities", []):
                p = Path(f"world/entities/{cat}/{eid}.json")
                if p.exists():
                    e = read_json(p)
                    names.add(e.get("name", "").strip().lower())
        except Exception:
            pass
    return names


def _build_gap_dashboard(state: dict,
                          entity_names: set[str] | None = None) -> str:
    if entity_names is None:
        entity_names = _load_entity_names()

    pending: list[tuple[int, str, int, int, str]] = []
    at_risk: list[tuple[str, int, int, str]] = []

    for metric, appear, _cat, name, remove in WORLD_GENERATION_RULES:
        if metric == "pollution":
            continue
        value = state.get(metric, 0)
        exists = name.strip().lower() in entity_names
        if not exists and value < appear:
            gap = appear - value
            pending.append((gap, metric, value, appear, name))
        elif exists and value <= remove + 4:
            at_risk.append((metric, value, remove, name))

    lines: list[str] = ["## What Needs Your Vote\n"]

    pending.sort()
    for gap, metric, value, appear, name in pending[:3]:
        lines.append(f"- **{name}** — {metric} {value}/{appear} (needs +{gap})")

    for metric, value, remove, name in at_risk:
        lines.append(f"- **{name}** at risk — {metric} {value} (removal if < {remove})")

    applied = state.get("tags_applied", [])
    for field, direction, threshold, tag_name in THRESHOLD_TAGS:
        if tag_name in applied:
            continue
        value = state.get(field, 0)
        if direction == "above":
            gap = threshold - value
            if 0 < gap <= 10:
                lines.append(f"- Milestone **{tag_name}** — {field} {value}/{threshold} "
                              f"(needs +{gap})")
        elif direction == "below":
            gap = value - threshold
            if 0 < gap <= 10:
                lines.append(f"- Milestone **{tag_name}** — {field} {value} -> {threshold} "
                              f"(needs -{gap})")

    if len(lines) == 1:
        lines.append("- All near-threshold goals reached — explore new frontiers!")

    return "\n".join(lines)


def get_or_create_dispatch_issue() -> int:
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "dispatch",
        "--state", "open", "--json", "number", "--limit", "1",
    ])
    if issues:
        return issues[0]["number"]
    body = (
        "World news dispatch for **Gitizens**.\n\n"
        "Every 4 hours, the world narrator updates this post with the latest "
        "tick summary — laws passed, events fired, population changes, and more.\n\n"
        "*React with 👍 to follow the chronicle.*"
    )
    tmp = Path("scripts/_dispatch_body.txt")
    tmp.write_text(body, encoding="utf-8")
    result = run(["gh", "issue", "create", "--repo", REPO,
                  "--title", "[World Chronicle] The Gitizens Dispatch",
                  "--label", "dispatch",
                  "--body-file", str(tmp)])
    tmp.unlink(missing_ok=True)
    try:
        num = int(result.strip().split("/")[-1])
        print(f"  Opened World Chronicle (#{num})")
        return num
    except (ValueError, IndexError):
        return 0


def _build_chronicle_body() -> str:
    dispatches_path = Path("world/dispatches.json")
    try:
        dispatches = json.loads(dispatches_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        dispatches = []

    lines = ["## World Chronicle — Latest Dispatches\n"]
    for d in reversed(dispatches[-5:]):
        tick = d.get("tick", "?")
        date = d.get("date", "")
        narrative = d.get("narrative", "")
        metrics = d.get("metrics", "")
        changes = d.get("changes", "")
        lines.append(f"**Tick {tick} · {date}**  ")
        lines.append(f"{narrative}\n")
        if metrics:
            lines.append(f"**Metrics:** {metrics}  ")
        if changes:
            lines.append(f"**Changes:** {changes}")
        lines.append("\n---\n")

    reps_path = Path("world/representatives.json")
    if reps_path.exists():
        try:
            reps = json.loads(reps_path.read_text(encoding="utf-8"))
            representatives = reps.get("representatives", [])
            if representatives:
                names = ", ".join(f"@{r}" for r in representatives)
                selected_at = reps.get("selected_at", "")
                lines.append("### Current Representatives\n")
                lines.append(f"{names} — elected {selected_at}\n")
                lines.append("\n---\n")
        except (json.JSONDecodeError, OSError):
            pass

    try:
        state = read_state()
        entity_names = _load_entity_names()
        lines.append(_build_gap_dashboard(state, entity_names))
        lines.append("\n---\n")
    except Exception:
        pass

    updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines.append(
        f"*Last updated: {updated} — full history in "
        "[dispatches.json](../../world/dispatches.json)*"
    )
    return "\n".join(lines)


def save_dispatch(state: dict, tick_changed: bool, laws_passed: int,
                  event_title: str, feedback_count: int):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = []
    try:
        history = json.loads(Path("world/history.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    tick_num = (history[-1]["tick"] + 1) if history else 1

    dispatches_path = Path("world/dispatches.json")
    try:
        dispatches = json.loads(dispatches_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        dispatches = []

    if dispatches and dispatches[-1].get("tick") == tick_num:
        print(f"  Dispatch for tick {tick_num} already saved — skipping")
        return

    metrics_str = (
        f"population {state.get('population', 0):,} · "
        f"treasury {state.get('treasury', 0)} GC · "
        f"stability {state.get('stability', 0)}/100 · "
        f"pollution {state.get('pollution', 0)}/100"
    )
    changes_parts = []
    if tick_changed:
        changes_parts.append("autonomous tick applied")
    if laws_passed:
        changes_parts.append(f"{laws_passed} law{'s' if laws_passed > 1 else ''} enacted")
    if feedback_count:
        changes_parts.append(f"{feedback_count} citizen feedback{'s' if feedback_count > 1 else ''} applied")
    if event_title:
        changes_parts.append(f"event: {event_title}")
    changes_summary = " · ".join(changes_parts) if changes_parts else "quiet tick"

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": (
            "You are the narrator of Gitizens, a GitHub-based civilization.\n"
            f"Tick #{tick_num} just completed on {today}.\n"
            f"World state: era={state.get('era')}, {metrics_str}\n"
            f"This tick: {changes_summary}\n\n"
            "Write a 2-3 sentence news dispatch in the style of a newspaper. "
            "Mention specific numbers. Tone: serious but vivid. No emoji, no markdown headers."
        )}],
        max_tokens=120,
        temperature=0.7,
    )
    narrative = response.choices[0].message.content.strip()

    dispatches.append({
        "tick": tick_num,
        "date": today,
        "narrative": narrative,
        "changes": changes_summary,
        "metrics": metrics_str,
    })
    if len(dispatches) > 10:
        dispatches = dispatches[-10:]
    dispatches_path.write_text(json.dumps(dispatches, indent=2) + "\n", encoding="utf-8")
    print(f"  Dispatch for tick {tick_num} saved")


def publish_dispatch():
    issue_num = get_or_create_dispatch_issue()
    if issue_num:
        upsert_bot_comment(issue_num, _build_chronicle_body())
        print(f"  Chronicle published on issue #{issue_num}")


def post_world_dispatch(state: dict, tick_changed: bool, laws_passed: int,
                        event_title: str, feedback_count: int):
    save_dispatch(state, tick_changed, laws_passed, event_title, feedback_count)
    publish_dispatch()


def collect_star_income():
    state = read_state()
    if state.get("treasury") is None:
        return

    raw = run(["gh", "api", f"repos/{REPO}/stargazers", "--paginate",
               "--jq", ".[].login"])
    current_logins = {line.strip() for line in raw.splitlines() if line.strip()}

    if state.get("known_stargazers") is None:
        state["known_stargazers"] = sorted(current_logins)
        state["stars_last_counted"] = len(current_logins)
        write_state(state)
        print(f"  Star tracking initialized: {len(current_logins)} existing stars, no income (first run)")
        return

    ever_starred = set(state["known_stargazers"])
    new_logins = current_logins - ever_starred

    state["known_stargazers"] = sorted(ever_starred | current_logins)
    state["stars_last_counted"] = len(current_logins)

    if not new_logins:
        write_state(state)
        return

    income = len(new_logins) * 10
    currency = state.get("currency", "Git Coins")
    state["treasury"] = min(100_000, state.get("treasury", 0) + income)
    write_state(state)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    generate_dashboard_svg(read_stats(), today)
    run(["git", "add", "world/state.json", "world/stats.svg"])
    run(["git", "commit", "-m",
         f"[WORLD] treasury: +{income} {currency} from {len(new_logins)} new star(s)"])
    print(f"  Star income: +{len(new_logins)} new stars -> +{income} {currency}")
