import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote as _url_quote

from openai import OpenAI

from .constants import CATEGORIES, POLICY_METRICS, BASE_STATE_FIELDS, ANNALS_INTERVAL
from .gh import run, gh_json, REPO, GITHUB_TOKEN
from .state import read_json, read_state, write_state
from .world import pollution_level


client = OpenAI(
    base_url="https://models.inference.ai.azure.com",
    api_key=GITHUB_TOKEN,
)

_LLM_EXCLUDE = {"known_stargazers", "tags_applied"}


def _state_for_llm(state: dict) -> dict:
    return {k: v for k, v in state.items() if k not in _LLM_EXCLUDE}


def generate_narrative(title: str, for_votes: int, against_votes: int, state: dict) -> str:
    metrics_str = " | ".join(f"{k}={state.get(k, 0)}" for k in sorted(POLICY_METRICS))
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": (
                "You are the narrator of a GitHub-based civilization called Gitizens.\n"
                "A new law has just been enacted by citizen vote.\n\n"
                f"World state: era={state.get('era')}, laws={state.get('laws_count')}, {metrics_str}\n"
                f"Title: {title}\n"
                f"Vote: {for_votes} for, {against_votes} against\n\n"
                "Write a 2-sentence news bulletin announcing this law's passage. "
                "Tone: serious newspaper. No emoji, no markdown headers."
            )}],
            max_tokens=150,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [WARN] generate_narrative failed: {e}")
        return f"Law enacted: {title}."


def update_world_summary(state: dict) -> str:
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": (
                "You are summarizing the state of a GitHub-based civilization called Gitizens.\n"
                f"Current state: {json.dumps(_state_for_llm(state), ensure_ascii=False)}\n\n"
                "Write a single sentence (max 25 words) describing the current state of the nation. "
                "Mention notable policy levels or emerging structures if relevant. No emoji."
            )}],
            max_tokens=70,
            temperature=0.7,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [WARN] update_world_summary failed: {e}")
        return ""


def generate_world_md(state: dict, law_number: int | None, date: str):
    lines: list[str] = ["# World State", ""]
    if law_number:
        lines.append(f"*Last updated: {date} — [Law {law_number:03d}](laws/law-{law_number:03d}.md)*")
    else:
        lines.append(f"*Last updated: {date}*")

    lines += ["", "---", "", "## Metrics", ""]
    lines += ["| Field | Value |", "|-------|-------|"]
    lines.append(f"| Era | {state.get('era', '')} |")
    lines.append(f"| Laws enacted | {state.get('laws_count', 0)} |")
    lines.append(f"| Last enacted | {state.get('last_enacted') or '—'} |")
    lines.append(f"| Treasury | {state.get('treasury', 0):,} {state.get('currency', 'Git Coins')} |")

    lines += ["", "### Policy", ""]
    lines += ["| Metric | Value |", "|--------|-------|"]
    for m in ("education", "industry", "welfare", "green_policy", "defense"):
        lines.append(f"| {m.replace('_', ' ').title()} | {state.get(m, 0)}/100 |")
    lines.append(f"| Pollution *(derived)* | {pollution_level(state)}/100 |")

    for k, v in state.items():
        if k not in BASE_STATE_FIELDS:
            lines.append(f"| {k.replace('_', ' ').title()} | {v} |")

    lines += ["", "---", "", "## Entities", ""]
    for cat, label in CATEGORIES:
        try:
            idx = read_json(Path(f"world/entities/{cat}/_index.json"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            idx = {}
        entity_ids = idx.get("entities", [])
        lines.append(f"### {label}")
        lines.append("")
        if entity_ids:
            lines += ["| ID | Name | Built by | Trigger |", "|----|------|----------|---------|"]
            for eid in entity_ids:
                path = Path(f"world/entities/{cat}/{eid}.json")
                if path.exists():
                    e = read_json(path)
                    law = e.get("built_law")
                    law_ref = f"[Law {law:03d}](laws/law-{law:03d}.md)" if law else "—"
                    trigger = e.get("auto_trigger", "—")
                    lines.append(f"| `{eid}` | {e.get('name', eid)} | {law_ref} | {trigger} |")
        else:
            lines.append("*(none yet)*")
        lines.append("")

    lines += ["---", "", "## Archive", ""]
    try:
        archived = sorted(f for f in Path("world/archive").glob("*.json") if f.name != ".gitkeep")
    except OSError:
        archived = []
    if archived:
        lines += ["| ID | Name | Demolished by | Reason |", "|----|------|---------------|--------|"]
        for f in archived:
            e = read_json(f)
            law = e.get("demolished_law")
            law_ref = f"[Law {law:03d}](laws/law-{law:03d}.md)" if law else "—"
            lines.append(f"| `{f.stem}` | {e.get('name', f.stem)} | {law_ref} | {e.get('auto_reason', '—')} |")
    else:
        lines.append("*(none)*")

    Path("world/WORLD.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _badge_url_val(s: str) -> str:
    return _url_quote(str(s).replace(" ", "_"), safe="")


def update_readme(state: dict, stats: dict, law_number: int | None = None, date: str = ""):
    readme_path = Path("README.md")
    content = readme_path.read_text(encoding="utf-8")

    era = state.get("era", "")
    laws_count = state.get("laws_count", 0)
    next_tick = state.get("next_tick_at", "—")
    prose_block = (
        f"**Era:** {era} | **Laws enacted:** {laws_count} | [World state](world/WORLD.md)  \n"
        f"**Next tick:** {next_tick} UTC"
    )
    content = re.sub(
        r"<!-- STATE_START -->.*?<!-- STATE_END -->",
        f"<!-- STATE_START -->\n{prose_block}\n<!-- STATE_END -->",
        content, flags=re.DOTALL,
    )

    era_b  = _badge_url_val(era)
    pop_b  = str(state.get("population", 0))
    trs_b  = str(state.get("treasury", 0))
    stb_b  = str(state.get("stability", 0))
    laws_b = str(laws_count)
    pol_b  = str(state.get("pollution", 0))
    badges = "\n".join([
        f"![Era](https://img.shields.io/badge/Era-{era_b}-e3b341?style=flat-square&logo=github)",
        f"![Population](https://img.shields.io/badge/Population-{pop_b}-3fb950?style=flat-square)",
        f"![Treasury](https://img.shields.io/badge/Treasury-{trs_b}_GC-388bfd?style=flat-square)",
        f"![Stability](https://img.shields.io/badge/Stability-{stb_b}%2F100-bc8cff?style=flat-square)",
        f"![Pollution](https://img.shields.io/badge/Pollution-{pol_b}%2F100-f85149?style=flat-square)",
        f"![Laws](https://img.shields.io/badge/Laws-{laws_b}_enacted-8b949e?style=flat-square)",
    ])
    content = re.sub(
        r"<!-- WORLD-STATE-START -->.*?<!-- WORLD-STATE-END -->",
        f"<!-- WORLD-STATE-START -->\n{badges}\n<!-- WORLD-STATE-END -->",
        content, flags=re.DOTALL,
    )

    readme_path.write_text(content, encoding="utf-8")


def generate_annals(history: list):
    if not history:
        return
    tick_num = history[-1].get("tick", len(history))
    if tick_num % ANNALS_INTERVAL != 0:
        return
    chapter_num = tick_num // ANNALS_INTERVAL
    chapter_path = Path(f"world/annals/chapter-{chapter_num:03d}.md")
    if chapter_path.exists():
        return
    recent = history[-ANNALS_INTERVAL:]
    state = read_state()
    summary_data = {
        "chapter": chapter_num,
        "ticks": f"{tick_num - ANNALS_INTERVAL + 1}–{tick_num}",
        "era": state.get("era"),
        "laws_in_period": recent[-1].get("laws_count", 0) - recent[0].get("laws_count", 0),
        "pop_change": recent[-1].get("population", 0) - recent[0].get("population", 0),
        "treasury_change": recent[-1].get("treasury", 0) - recent[0].get("treasury", 0),
    }
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": (
                "You are the official historian of Gitizens.\n"
                f"Write World Annals Chapter {chapter_num}, covering ticks {summary_data['ticks']}.\n"
                f"Data: {json.dumps(summary_data)}\n"
                f"Current world: {json.dumps(_state_for_llm(state))}\n\n"
                "Format:\n"
                f"# World Annals — Chapter {chapter_num}\n\n"
                "## Summary\n<3-4 sentences of narrative history>\n\n"
                "## Key Events\n<bullet points of notable changes>\n\n"
                "## Citizen Voices\n<2 short quotes from fictional citizens, different perspectives>"
            )}],
            max_tokens=400,
            temperature=0.7,
        )
        text = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [WARN] generate_annals LLM failed: {e}")
        return
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text(text + "\n", encoding="utf-8")
    print(f"  Annals chapter {chapter_num} written to {chapter_path}")


# ── Pinned comment helpers ────────────────────────────────────────────────────

_PINNED_IDS_PATH = Path("world/pinned_comment_ids.json")


def _load_pinned_ids() -> dict:
    try:
        return json.loads(_PINNED_IDS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def upsert_bot_comment(issue_num: int, body: str):
    ids = _load_pinned_ids()
    comment_id = ids.get(str(issue_num))
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
        tf.write(json.dumps({"body": body}))
        payload_path = tf.name
    if comment_id:
        result = run(["gh", "api", "--method", "PATCH",
                      f"repos/{REPO}/issues/comments/{comment_id}",
                      "--input", payload_path])
        Path(payload_path).unlink(missing_ok=True)
        if result:
            print(f"  Updated pinned comment #{comment_id} on issue #{issue_num}")
            return
        print(f"  [WARN] PATCH failed for comment #{comment_id}, posting new")
    else:
        Path(payload_path).unlink(missing_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tf:
        tf.write(body)
        body_path = tf.name
    result = run(["gh", "issue", "comment", str(issue_num), "--repo", REPO,
                  "--body-file", body_path])
    Path(body_path).unlink(missing_ok=True)
    m = re.search(r"issuecomment-(\d+)", result)
    if m:
        new_id = int(m.group(1))
        ids[str(issue_num)] = new_id
        _PINNED_IDS_PATH.write_text(json.dumps(ids, indent=2) + "\n", encoding="utf-8")
        print(f"  Posted and pinned comment #{new_id} on issue #{issue_num}")
    else:
        print(f"  Posted new comment to issue #{issue_num}")


def _get_or_create_citizen_voices_issue() -> int:
    run(["gh", "label", "create", "citizen-voices", "--repo", REPO,
         "--color", "d93f0b", "--description", "Weekly citizen diary", "--force"])
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "citizen-voices",
        "--state", "open", "--json", "number", "--limit", "1",
    ])
    if issues:
        return issues[0]["number"]
    tmp = Path("scripts/_cv_body.txt")
    tmp.write_text(
        "Weekly diary entries from fictional Gitizens citizens, "
        "updated every 7 days by the world engine.",
        encoding="utf-8",
    )
    result = run(["gh", "issue", "create", "--repo", REPO,
                  "--title", "[Citizen Voices] Weekly Diary",
                  "--label", "citizen-voices",
                  "--body-file", str(tmp)])
    tmp.unlink(missing_ok=True)
    try:
        num = int(result.strip().split("/")[-1])
        print(f"  Opened Citizen Voices issue (#{num})")
        return num
    except (ValueError, IndexError):
        return 0


def generate_citizen_narrator():
    state = read_state()
    last_narrator = state.get("last_narrator_date")
    if last_narrator:
        last_dt = datetime.fromisoformat(last_narrator.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=timezone.utc)
        if (datetime.now(timezone.utc) - last_dt).days < 7:
            return
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": (
                "You are writing citizen perspectives for Gitizens.\n"
                f"World state: {json.dumps(_state_for_llm(state))}\n\n"
                "Write 3 short diary entries (2-3 sentences each) from 3 different fictional citizens:\n"
                "1. A government official\n2. A factory worker\n3. A teacher\n\n"
                "Each entry: '**[Name], [Occupation]:**\\n<diary entry>'\n"
                "Make them react to the current world metrics and recent laws. Tone: vivid, personal. No emoji."
            )}],
            max_tokens=300,
            temperature=0.9,
        )
        narrative = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  [WARN] generate_citizen_narrator failed: {e}")
        return
    body = (
        f"## Citizen Voices — {today_str}\n\n"
        f"{narrative}\n\n"
        f"*These are fictional citizen perspectives generated by the world engine. "
        f"Updated weekly.*"
    )
    issue_num = _get_or_create_citizen_voices_issue()
    if issue_num:
        upsert_bot_comment(issue_num, body)
    state["last_narrator_date"] = datetime.now(timezone.utc).isoformat()
    write_state(state)
    print("  Citizen narrator updated")
