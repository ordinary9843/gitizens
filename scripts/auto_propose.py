#!/usr/bin/env python3
"""
AI citizen proposal generator.
Called from tally_votes.py main() at the end of each tick.
Generates [AI-PROPOSAL] and [FEEDBACK] GitHub Issues using GitHub Models API.
"""
import json
import subprocess
from pathlib import Path
from openai import OpenAI

POLICY_METRICS = ["education", "industry", "welfare", "green_policy", "defense"]
MAX_DELTA_PROPOSAL = 8
MAX_DELTA_FEEDBACK = 2


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 and result.stderr.strip():
        print(f"  [WARN] {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def _gh_json(cmd: list[str]) -> list | dict:
    out = _run(["gh", *cmd])
    return json.loads(out) if out else []


def _open_count(repo: str, label: str) -> int:
    items = _gh_json([
        "issue", "list", "--repo", repo, "--label", label,
        "--state", "open", "--json", "number", "--limit", "50",
    ])
    return len(items)


def should_generate(repo: str) -> tuple[bool, bool]:
    """Return (should_ai_proposal, should_feedback)."""
    return (
        _open_count(repo, "ai-proposal") == 0,
        _open_count(repo, "feedback") < 2,
    )


def _post_issue(repo: str, title: str, body: str, label: str) -> int:
    tmp = Path("scripts/_ai_body.txt")
    tmp.write_text(body, encoding="utf-8")
    result = _run(["gh", "issue", "create", "--repo", repo,
                   "--title", title, "--label", label, "--body-file", str(tmp)])
    tmp.unlink(missing_ok=True)
    try:
        return int(result.strip().split("/")[-1])
    except (ValueError, IndexError):
        return 0


def generate_ai_proposal(client: OpenAI, state: dict, repo: str) -> int:
    """Generate one [AI-PROPOSAL] issue targeting the weakest policy metric."""
    weakest = min(POLICY_METRICS, key=lambda m: state.get(m, 0))
    weakest_val = state.get(weakest, 0)
    metrics_str = ", ".join(f"{m}={state.get(m,0)}" for m in POLICY_METRICS)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": (
                "You are an AI citizen of Gitizens, a GitHub-based civilization.\n"
                f"Current world: era={state.get('era')}, treasury={state.get('treasury')} GC, "
                f"population={state.get('population')}, stability={state.get('stability')}\n"
                f"Policy metrics: {metrics_str}\n\n"
                f"The weakest metric is '{weakest}' at {weakest_val}/100. "
                "Propose a policy law to address this.\n\n"
                "Respond in JSON with exactly these keys:\n"
                '{"title": "short law title (no prefix)", '
                '"description": "2-3 sentence description of what this law does and why", '
                f'"delta": <integer +5 to +{MAX_DELTA_PROPOSAL} for the metric>}}\n\n'
                "The title should be creative and specific. No markdown in title."
            )}],
            max_tokens=200,
            temperature=0.8,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        title = data.get("title", f"Strengthen {weakest.replace('_',' ').title()}")
        description = data.get("description", "An AI citizen proposal to improve national policy.")
        delta = max(3, min(MAX_DELTA_PROPOSAL, int(data.get("delta", 5))))
    except Exception as e:
        print(f"  [WARN] AI proposal generation failed: {e}")
        title = f"Strengthen {weakest.replace('_', ' ').title()}"
        description = f"An AI citizen proposal to improve {weakest} from {weakest_val} to {weakest_val + 5}."
        delta = 5

    body = (
        f"## Description\n\n{description}\n\n"
        f"*This proposal was submitted by an AI citizen. "
        f"React with 👎 within 2 hours to veto it.*\n\n"
        f"## Effect\n\n"
        f"```yaml\ntype: policy\nchanges:\n  {weakest}: +{delta}\n```\n"
    )
    issue_num = _post_issue(repo, f"[AI-PROPOSAL] {title}", body, "ai-proposal")
    print(f"  AI proposal #{issue_num}: {title} ({weakest} +{delta})")
    return issue_num


def generate_feedbacks(client: OpenAI, state: dict, repo: str, count: int = 2) -> list[int]:
    """Generate citizen feedback Issues with small effects."""
    metrics_str = ", ".join(f"{m}={state.get(m,0)}" for m in POLICY_METRICS)
    pollution = state.get("pollution", 0)
    stability = state.get("stability", 80)

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": (
                "You are generating citizen feedback for Gitizens, a GitHub-based civilization.\n"
                f"World: era={state.get('era')}, population={state.get('population')}, "
                f"pollution={pollution}, stability={stability}\n"
                f"Policy metrics: {metrics_str}\n\n"
                f"Generate exactly {count} distinct citizen feedback items. "
                "Each is a small real-world observation from a citizen's perspective "
                "(noise complaints, local events, small social changes, etc.).\n\n"
                "Respond in JSON with key 'feedbacks' as an array. Each item:\n"
                '{"title": "short event title", '
                '"description": "1-2 sentence citizen observation", '
                '"metric": "<one of: education,industry,welfare,green_policy,defense,stability,pollution>", '
                '"delta": <integer -2 to +2, nonzero>}\n\n'
                "Mix positive and negative. Keep deltas small (±1 or ±2)."
            )}],
            max_tokens=400,
            temperature=0.9,
            response_format={"type": "json_object"},
        )
        data = json.loads(response.choices[0].message.content)
        feedbacks = data.get("feedbacks", [])[:count]
    except Exception as e:
        print(f"  [WARN] Feedback generation failed: {e}")
        feedbacks = []

    valid_metrics = set(POLICY_METRICS) | {"stability", "pollution"}
    issue_numbers = []
    for fb in feedbacks:
        try:
            title = fb.get("title", "Citizen Feedback")
            description = fb.get("description", "Citizens report a minor change in daily life.")
            metric = fb.get("metric", "welfare")
            if metric not in valid_metrics:
                metric = "welfare"
            delta = max(-MAX_DELTA_FEEDBACK, min(MAX_DELTA_FEEDBACK, int(fb.get("delta", 1))))
            if delta == 0:
                delta = 1
        except (ValueError, TypeError):
            continue

        sign = "+" if delta > 0 else ""
        body = (
            f"## Description\n\n{description}\n\n"
            f"*Citizens report this small change in Gitizens. "
            f"React with 👎 within 2 hours to dismiss it.*\n\n"
            f"## Effect\n\n"
            f"```yaml\ntype: policy\nchanges:\n  {metric}: {sign}{delta}\n```\n"
        )
        issue_num = _post_issue(repo, f"[FEEDBACK] {title}", body, "feedback")
        if issue_num:
            print(f"  Feedback #{issue_num}: {title} ({metric} {sign}{delta})")
            issue_numbers.append(issue_num)

    return issue_numbers
