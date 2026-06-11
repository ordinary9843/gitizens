#!/usr/bin/env python3
"""
Validate a GitHub Issue as a Gitizens proposal.
Called by validate-proposal.yml on issues.opened.
"""
import os
import sys
import json
import re
import subprocess
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path
from openai import OpenAI

ISSUE_NUMBER = os.environ["ISSUE_NUMBER"]
ISSUE_TITLE  = os.environ["ISSUE_TITLE"]
ISSUE_BODY   = os.environ.get("ISSUE_BODY", "")
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO         = os.environ["GITHUB_REPOSITORY"]

VALID_TYPES    = {"declaration", "policy", "evolve", "state_patch"}
POLICY_METRICS = {"education", "industry", "welfare", "green_policy", "defense"}
POLICY_COST    = 100

REQUIRED_FIELDS = {
    "policy":      ["changes"],
    "evolve":      ["id", "changes"],
    "state_patch": ["patch"],
    "declaration": [],
}

COOLDOWN_DAYS = 14


def gh(*args):
    subprocess.run(["gh", *args], check=False)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fail(reason: str):
    print(f"INVALID: {reason}", file=sys.stderr)
    gh("issue", "comment", ISSUE_NUMBER, "--repo", REPO, "--body",
       f"This proposal was automatically closed.\n\n**Reason:** {reason}\n\n"
       "Use `/gitizens:propose` to submit a valid proposal.")
    gh("issue", "close", ISSUE_NUMBER, "--repo", REPO)
    gh("issue", "edit", ISSUE_NUMBER, "--repo", REPO, "--add-label", "invalid")
    sys.exit(0)


def check_cooldown_for_proposal(effect_data: dict) -> tuple[bool, str]:
    if not effect_data or effect_data.get("type") != "policy":
        return True, ""
    path = Path("world/proposal_cooldowns.json")
    if not path.exists():
        return True, ""
    try:
        cooldowns = json.loads(path.read_text(encoding="utf-8"))
        today = datetime.now(timezone.utc).date()
        for metric in effect_data.get("changes", {}):
            if metric not in cooldowns:
                continue
            last_date = datetime.fromisoformat(cooldowns[metric]).date()
            if (today - last_date).days < COOLDOWN_DAYS:
                until = (last_date + timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%d")
                return False, f"metric '{metric}' on cooldown until {until}"
    except Exception:
        pass
    return True, ""


def load_world_context() -> dict:
    ctx: dict = {}
    try:
        ctx["state"] = read_json(Path("world/state.json"))
    except Exception:
        ctx["state"] = {}
    entities: dict[str, list[str]] = {}
    for cat in ("buildings", "districts", "institutions", "sectors"):
        try:
            idx = read_json(Path(f"world/entities/{cat}/_index.json"))
            names = []
            for eid in idx.get("entities", []):
                p = Path(f"world/entities/{cat}/{eid}.json")
                if p.exists():
                    e = read_json(p)
                    names.append(f"{eid}: {e.get('name', eid)}")
            entities[cat] = names
        except Exception:
            entities[cat] = []
    ctx["entities"] = entities
    return ctx


def validate():
    if not ISSUE_TITLE.startswith("[PROPOSAL]"):
        fail("Title must start with `[PROPOSAL]`.")

    desc_match = re.search(r"## Description\s+(.*?)(?=\n##|\Z)", ISSUE_BODY, re.DOTALL)
    if not desc_match or not desc_match.group(1).strip():
        fail("Missing or empty `## Description` section.")
    description = desc_match.group(1).strip()
    if len(description) < 30:
        fail("Description is too short. Explain what this law does and why citizens should vote for it.")

    effect_data = None
    effect_match = re.search(r"## Effect\s+```ya?ml\s+(.*?)```", ISSUE_BODY, re.DOTALL)
    if effect_match:
        try:
            effect_data = yaml.safe_load(effect_match.group(1).strip())
        except yaml.YAMLError as exc:
            fail(f"Invalid YAML in `## Effect` section: {exc}")
            return

        if not isinstance(effect_data, dict):
            fail("Effect YAML must be a mapping (key: value pairs).")

        effect_type = effect_data.get("type")
        if effect_type not in VALID_TYPES:
            fail(f"Unknown effect type `{effect_type}`. Valid types: {', '.join(sorted(VALID_TYPES))}")

        for field in REQUIRED_FIELDS.get(effect_type, []):
            if field not in effect_data:
                fail(f"Effect type `{effect_type}` requires field `{field}`.")

        if effect_type == "policy":
            changes = effect_data.get("changes", {})
            if not isinstance(changes, dict) or not changes:
                fail("Policy `changes` must be a non-empty mapping.")
            for key in changes:
                if key not in POLICY_METRICS:
                    fail(f"Unknown policy metric `{key}`. "
                         f"Valid metrics: {', '.join(sorted(POLICY_METRICS))}")
            for key, val in changes.items():
                try:
                    delta = int(val)
                except (TypeError, ValueError):
                    fail(f"Policy change `{key}` must be an integer (e.g. +20 or -10).")
                if abs(delta) > 50:
                    fail(f"Policy change `{key}: {val}` exceeds the ±50 limit per proposal.")

        if effect_type == "evolve":
            entity_id = effect_data.get("id", "")
            found = any(
                Path(f"world/entities/{cat}/{entity_id}.json").exists()
                for cat in ("buildings", "districts", "institutions", "sectors")
            )
            if not found:
                fail(f"Entity `{entity_id}` does not exist. "
                     "Check [world/WORLD.md](world/WORLD.md) for valid entity IDs.")

    # LLM contextual validation
    ctx = load_world_context()
    state_summary = json.dumps(ctx["state"], ensure_ascii=False)
    entity_lines = [f"{c}: {', '.join(n)}" for c, n in ctx["entities"].items() if n]
    entity_summary = "\n".join(entity_lines) if entity_lines else "No structures built yet."
    effect_summary = ""
    if effect_data:
        effect_summary = (f"\nEffect type: {effect_data.get('type')}\n"
                          f"Effect data: {json.dumps(effect_data, ensure_ascii=False)}")

    client = OpenAI(base_url="https://models.inference.ai.azure.com", api_key=GITHUB_TOKEN)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": (
            "You are a validator for a GitHub-based civilization called Gitizens.\n\n"
            f"Current world state: {state_summary}\n"
            f"Existing structures:\n{entity_summary}\n\n"
            "Evaluate this proposal:\n"
            f"Title: {ISSUE_TITLE}\n"
            f"Description: {description}{effect_summary}\n\n"
            "Check ALL of the following:\n"
            "1. Does the proposal have a clear, actionable voting intent?\n"
            "2. Is it coherent and meaningful in the context of the current world?\n"
            "3. For policy proposals: are the metric changes plausible given the description?\n\n"
            "Fail ONLY if there is a concrete, specific problem with one of the above.\n"
            "Creative, humorous, or controversial proposals are VALID as long as the intent is clear.\n"
            'Reply in JSON only: {"valid": true/false, "reason": "one sentence"}'
        )}],
        max_tokens=120,
        temperature=0,
    )
    raw = response.choices[0].message.content.strip()
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"valid": True}

    if not result.get("valid", True):
        fail(f"{result.get('reason', 'Proposal did not pass review.')}")

    # Treasury notice for policy proposals
    if effect_data and effect_data.get("type") == "policy":
        try:
            state    = read_json(Path("world/state.json"))
            treasury = state.get("treasury", 0)
            currency = state.get("currency", "Git Coins")
            if treasury >= POLICY_COST:
                status = f"Treasury check passed. Current balance: **{treasury} {currency}**."
            else:
                status = (f"Treasury insufficient. Balance: **{treasury} {currency}** "
                          f"— short by **{POLICY_COST - treasury} {currency}**. "
                          f"This proposal will be blocked at tally unless the treasury is replenished.")
            gh("issue", "comment", ISSUE_NUMBER, "--repo", REPO, "--body",
               f"**Cost notice:** Enacting this policy costs **{POLICY_COST} {currency}**.\n\n{status}")
        except Exception:
            pass

    if effect_data and effect_data.get("type") == "policy":
        ok, reason = check_cooldown_for_proposal(effect_data)
        if not ok:
            fail(f"Proposal cooldown active: {reason}")

    print("VALID — applying proposal label")
    gh("issue", "edit", ISSUE_NUMBER, "--repo", REPO, "--add-label", "proposal")


if __name__ == "__main__":
    validate()
