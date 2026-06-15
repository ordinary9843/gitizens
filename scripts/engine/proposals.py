import json
import re
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .constants import VOTING_PERIOD_DAYS, AI_VOTING_HOURS, POLICY_METRICS, POLICY_COST
from .gh import run, gh_json, get_reactions, REPO, SKIP_TIMING
from . import gh as _gh
from .state import read_state, write_state, read_stats, write_stats
from .world import determine_era, check_threshold_tags, run_world_engine, apply_effect, apply_tags
from .state import load_active_event
from .events import apply_crisis_multiplier
from .content import generate_narrative, update_world_summary, generate_world_md, update_readme
from .chronicle import append_history, update_laws_index
from .citizens import (
    format_signatories, track_citizen_activity, track_citizen_proposal,
    check_proposal_cooldown, update_proposal_cooldown,
)


def parse_effect(body: str) -> dict | None:
    m = re.search(r"## Effect\s+```ya?ml\s+(.*?)```", body, re.DOTALL)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1).strip())
        return data if isinstance(data, dict) else None
    except yaml.YAMLError:
        return None


def next_law_number() -> int:
    return read_state().get("laws_count", 0) + 1


def get_open_proposals() -> list:
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "proposal",
        "--state", "open", "--json", "number,title,body,createdAt,author", "--limit", "100",
    ])
    return sorted(issues, key=lambda x: x["number"])


def get_ai_proposals() -> list:
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "ai-proposal",
        "--state", "open", "--json", "number,title,body,createdAt", "--limit", "50",
    ])
    return sorted(issues, key=lambda x: x["number"])


def get_feedbacks() -> list:
    issues = gh_json([
        "issue", "list", "--repo", REPO, "--label", "feedback",
        "--state", "open", "--json", "number,title,body,createdAt", "--limit", "50",
    ])
    return sorted(issues, key=lambda x: x["number"])


def process_issue(issue: dict):
    number = issue["number"]
    title  = issue["title"]
    body   = issue.get("body") or ""
    created_at = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    reps: list[str] = []
    reps_path = Path("world/representatives.json")
    if reps_path.exists():
        try:
            reps = json.loads(reps_path.read_text(encoding="utf-8")).get("representatives", [])
        except Exception:
            pass
    proposer_login = issue.get("author", {}).get("login", "") or ""
    voting_hours = 12 if proposer_login in reps else VOTING_PERIOD_DAYS * 24
    if not SKIP_TIMING and (now - created_at) < timedelta(hours=voting_hours):
        print(f"  #{number}: voting period not over, skipping")
        return

    for_votes, against_votes, for_voters, against_voters = get_reactions(number)
    today = now.strftime("%Y-%m-%d")
    clean_title = re.sub(r"^\[PROPOSAL\]\s*", "", title).strip()

    if for_votes == 0 and against_votes == 0:
        print(f"  #{number}: zero votes — silent close")
        stats = read_stats()
        stats["proposals_silent"] = stats.get("proposals_silent", 0) + 1
        write_stats(stats)
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", "No votes were cast. Proposal closed without record."])
        run(["gh", "issue", "close", str(number), "--repo", REPO])
        run(["gh", "issue", "edit", str(number), "--repo", REPO, "--remove-label", "proposal"])
        return

    effect_data = parse_effect(body)

    if for_votes > against_votes:
        law_number   = next_law_number()
        state_before = read_state()

        extra_cost = 0
        if effect_data and effect_data.get("type") == "policy":
            ok, reason, extra_cost = check_proposal_cooldown(effect_data)
            if not ok:
                print(f"  #{number}: COOLDOWN BLOCKED at tally — {reason}")
                stats = read_stats()
                stats["proposals_total"]    = stats.get("proposals_total", 0) + 1
                stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
                write_stats(stats)
                run(["gh", "issue", "comment", str(number), "--repo", REPO,
                     "--body", f"**Proposal blocked: metric on cooldown.**\n\n{reason}"])
                run(["gh", "issue", "edit", str(number), "--repo", REPO,
                     "--add-label", "rejected", "--remove-label", "proposal"])
                run(["gh", "issue", "close", str(number), "--repo", REPO])
                return

            treasury = state_before.get("treasury", 0)
            currency = state_before.get("currency", "Git Coins")
            total_cost = POLICY_COST + extra_cost
            if treasury < total_cost:
                print(f"  #{number}: TREASURY BLOCKED — needs {total_cost}, has {treasury}")
                stats = read_stats()
                stats["proposals_total"]    = stats.get("proposals_total", 0) + 1
                stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
                write_stats(stats)
                penalty_note = (
                    f" (base **{POLICY_COST}** + repeat-touch surcharge **{extra_cost}**)"
                    if extra_cost > 0 else ""
                )
                run(["gh", "issue", "comment", str(number), "--repo", REPO,
                     "--body",
                     f"**Proposal blocked: insufficient treasury.**\n\n"
                     f"Enacting this policy costs **{total_cost} {currency}**{penalty_note}.\n"
                     f"Current treasury: **{treasury} {currency}**.\n\n"
                     f"Pass a treasury replenishment proposal first:\n"
                     f"```yaml\ntype: state_patch\npatch:\n  treasury: {treasury + total_cost + 200}\n```"])
                run(["gh", "issue", "edit", str(number), "--repo", REPO,
                     "--add-label", "rejected", "--remove-label", "proposal"])
                run(["gh", "issue", "close", str(number), "--repo", REPO])
                return

        print(f"  #{number}: PASSED ({for_votes}+1 {against_votes}-1) -> law-{law_number:03d}")
        narrative = generate_narrative(clean_title, for_votes, against_votes, state_before)

        active_event_now = load_active_event()
        effect_data = apply_crisis_multiplier(effect_data, active_event_now)
        apply_effect(effect_data, law_number, extra_cost=extra_cost)
        world_changes = run_world_engine(law_number)

        state = read_state()
        state["era"]          = determine_era(state)
        state["laws_count"]   = law_number
        state["last_enacted"] = today
        state["world_summary"] = update_world_summary(state)
        threshold_tags = check_threshold_tags(state_before, state)

        stats = read_stats()
        stats["proposals_total"]  = stats.get("proposals_total", 0) + 1
        stats["proposals_passed"] = stats.get("proposals_passed", 0) + 1
        write_stats(stats)

        generate_world_md(state, law_number, today)
        update_readme(state, stats, law_number, today)

        issue_url = f"https://github.com/{REPO}/issues/{number}"
        cost_line = ""
        if effect_data and effect_data.get("type") == "policy":
            currency  = state_before.get("currency", "Git Coins")
            total_cost = POLICY_COST + extra_cost
            cost_breakdown = (
                f" (base {POLICY_COST} + surcharge {extra_cost})" if extra_cost > 0 else ""
            )
            cost_line = (
                f"**Treasury:** -{total_cost} {currency}{cost_breakdown} "
                f"(balance: {state.get('treasury', 0)} {currency})  \n"
            )

        proposer = issue.get("author", {}).get("login") or ""
        proposer_display = f"@{proposer}" if proposer else "*(unknown)*"
        signatories_block = format_signatories(for_voters, against_voters)
        try:
            Path(f"world/laws/law-{law_number:03d}.md").write_text(
                f"# Law {law_number:03d}: {clean_title}\n\n"
                f"**Enacted:** {today}  \n"
                f"**Proposal:** [#{number}]({issue_url})  \n"
                f"**Proposed by:** {proposer_display}  \n"
                f"**Vote:** {for_votes} for, {against_votes} against  \n"
                f"{cost_line}"
                f"{signatories_block}\n"
                "\n---\n\n"
                f"{body}\n\n"
                "---\n\n"
                f"*{narrative}*\n",
                encoding="utf-8",
            )
        except OSError as e:
            print(f"  [ERROR] Failed to write law file for #{number}: {e} — aborting")
            return
        write_state(state)

        append_history(law_number, clean_title, number, for_votes, against_votes, True, today)
        update_laws_index(law_number, clean_title, number, issue_url, state["era"], today)
        track_citizen_activity(for_voters, against_voters)
        if proposer:
            track_citizen_proposal(proposer)
        run(["git", "add", "-A"])
        run(["git", "commit", "-m",
             f"[LAW] law-{law_number:03d}: {clean_title} (#{number})"])
        update_proposal_cooldown(effect_data, today)
        apply_tags(effect_data, state_before, state, law_number, clean_title, threshold_tags)

        world_note = ("\n\n**World changes:** " + ", ".join(world_changes)) if world_changes else ""
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body",
             f"**Law {law_number:03d} enacted.** Vote: {for_votes}+1 {against_votes}-1\n\n"
             f"{narrative}{world_note}"])
        run(["gh", "issue", "edit", str(number), "--repo", REPO,
             "--add-label", "passed", "--remove-label", "proposal"])
        run(["gh", "issue", "close", str(number), "--repo", REPO])

    else:
        print(f"  #{number}: REJECTED ({for_votes}+1 {against_votes}-1)")
        stats = read_stats()
        stats["proposals_total"]    = stats.get("proposals_total", 0) + 1
        stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
        write_stats(stats)

        state = read_state()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        generate_world_md(state, None, today)
        update_readme(state, stats, None, today)
        append_history(None, clean_title, number, for_votes, against_votes, False, today)
        run(["git", "add", "-A"])
        run(["git", "commit", "-m", f"[REJECTED] {clean_title} (#{number})"])
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", f"Proposal rejected. Vote: {for_votes}+1 {against_votes}-1"])
        run(["gh", "issue", "edit", str(number), "--repo", REPO,
             "--add-label", "rejected", "--remove-label", "proposal"])
        run(["gh", "issue", "close", str(number), "--repo", REPO])


def process_ai_proposal(issue: dict):
    number = issue["number"]
    title  = issue["title"]
    body   = issue.get("body") or ""
    created_at = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    if not SKIP_TIMING and (now - created_at) < timedelta(hours=AI_VOTING_HOURS):
        print(f"  AI-proposal #{number}: window not over, skipping")
        return

    _, against_votes, _, against_voters = get_reactions(number)
    today = now.strftime("%Y-%m-%d")
    clean_title = re.sub(r"^\[AI-PROPOSAL\]\s*", "", title).strip()
    issue_url = f"https://github.com/{REPO}/issues/{number}"

    if against_votes > 0:
        print(f"  AI-proposal #{number}: VETOED ({against_votes} against)")
        stats = read_stats()
        stats["proposals_total"]    = stats.get("proposals_total", 0) + 1
        stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
        write_stats(stats)
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", f"**AI proposal vetoed** by citizen vote ({against_votes} 👎). No effect applied."])
        run(["gh", "issue", "edit", str(number), "--repo", REPO,
             "--add-label", "rejected", "--remove-label", "ai-proposal"])
        run(["gh", "issue", "close", str(number), "--repo", REPO])
        return

    law_number   = next_law_number()
    state_before = read_state()
    effect_data  = parse_effect(body)

    extra_cost = 0
    if effect_data and effect_data.get("type") == "policy":
        ok, reason, extra_cost = check_proposal_cooldown(effect_data)
        if not ok:
            print(f"  AI-proposal #{number}: COOLDOWN BLOCKED — {reason}")
            stats = read_stats()
            stats["proposals_total"]    = stats.get("proposals_total", 0) + 1
            stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
            write_stats(stats)
            run(["gh", "issue", "comment", str(number), "--repo", REPO,
                 "--body", f"**AI proposal blocked: metric on cooldown.**\n\n{reason}"])
            run(["gh", "issue", "edit", str(number), "--repo", REPO,
                 "--add-label", "rejected", "--remove-label", "ai-proposal"])
            run(["gh", "issue", "close", str(number), "--repo", REPO])
            return

        treasury = state_before.get("treasury", 0)
        currency = state_before.get("currency", "Git Coins")
        total_cost = POLICY_COST + extra_cost
        if treasury < total_cost:
            print(f"  AI-proposal #{number}: TREASURY BLOCKED — needs {total_cost}, has {treasury}")
            stats = read_stats()
            stats["proposals_total"]    = stats.get("proposals_total", 0) + 1
            stats["proposals_rejected"] = stats.get("proposals_rejected", 0) + 1
            write_stats(stats)
            penalty_note = (
                f" (base **{POLICY_COST}** + repeat-touch surcharge **{extra_cost}**)"
                if extra_cost > 0 else ""
            )
            run(["gh", "issue", "comment", str(number), "--repo", REPO,
                 "--body",
                 f"**AI proposal blocked: insufficient treasury.**\n\n"
                 f"Enacting this policy costs **{total_cost} {currency}**{penalty_note}.\n"
                 f"Current treasury: **{treasury} {currency}**.\n\n"
                 f"A treasury replenishment proposal is needed:\n"
                 f"```yaml\ntype: state_patch\npatch:\n  treasury: {treasury + total_cost + 200}\n```"])
            run(["gh", "issue", "edit", str(number), "--repo", REPO,
                 "--add-label", "rejected", "--remove-label", "ai-proposal"])
            run(["gh", "issue", "close", str(number), "--repo", REPO])
            return

    print(f"  AI-proposal #{number}: PASSED (no veto) -> law-{law_number:03d}")
    narrative = generate_narrative(clean_title, 0, 0, state_before)
    active_event_now = load_active_event()
    effect_data = apply_crisis_multiplier(effect_data, active_event_now)
    apply_effect(effect_data, law_number, extra_cost=extra_cost)
    world_changes = run_world_engine(law_number)

    state = read_state()
    state["era"]           = determine_era(state)
    state["laws_count"]    = law_number
    state["last_enacted"]  = today
    state["world_summary"] = update_world_summary(state)
    threshold_tags = check_threshold_tags(state_before, state)

    stats = read_stats()
    stats["proposals_total"]  = stats.get("proposals_total", 0) + 1
    stats["proposals_passed"] = stats.get("proposals_passed", 0) + 1
    write_stats(stats)

    # Close and relabel FIRST so a mid-processing error cannot cause re-processing.
    run(["gh", "issue", "edit", str(number), "--repo", REPO,
         "--add-label", "passed", "--remove-label", "ai-proposal"])
    run(["gh", "issue", "close", str(number), "--repo", REPO])

    try:
        Path(f"world/laws/law-{law_number:03d}.md").write_text(
            f"# Law {law_number:03d}: {clean_title}\n\n"
            f"**Enacted:** {today}  \n"
            f"**Proposal:** [#{number}]({issue_url})  \n"
            f"**Proposed by:** AI citizen  \n"
            f"**Vote:** auto-passed (no veto)  \n"
            "\n---\n\n"
            f"{body}\n\n"
            "---\n\n"
            f"*{narrative}*\n",
            encoding="utf-8",
        )
    except OSError as e:
        print(f"  [ERROR] Failed to write law file for AI-proposal #{number}: {e} — aborting")
        return
    write_state(state)
    generate_world_md(state, law_number, today)
    update_readme(state, stats, law_number, today)
    update_laws_index(law_number, clean_title, number, issue_url, state["era"], today)
    update_proposal_cooldown(effect_data, today)
    append_history(law_number, clean_title, number, 0, 0, True, today)
    run(["git", "add", "-A"])
    run(["git", "commit", "-m", f"[LAW] law-{law_number:03d}: {clean_title} (AI, #{number})"])
    apply_tags(effect_data, state_before, state, law_number, clean_title, threshold_tags)

    world_note = ("\n\n**World changes:** " + ", ".join(world_changes)) if world_changes else ""
    run(["gh", "issue", "comment", str(number), "--repo", REPO,
         "--body",
         f"**Law {law_number:03d} enacted** (AI proposal — no veto received).\n\n"
         f"{narrative}{world_note}"])


def process_feedback(issue: dict) -> bool:
    number = issue["number"]
    title  = issue["title"]
    body   = issue.get("body") or ""
    created_at = datetime.fromisoformat(issue["createdAt"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)

    if not SKIP_TIMING and (now - created_at) < timedelta(hours=AI_VOTING_HOURS):
        print(f"  Feedback #{number}: window not over, skipping")
        return False

    _, against_votes, for_voters, against_voters = get_reactions(number)
    clean_title = re.sub(r"^\[FEEDBACK\]\s*", "", title).strip()
    track_citizen_activity(for_voters, against_voters)

    if against_votes > 0:
        print(f"  Feedback #{number}: DISMISSED ({against_votes} against)")
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", f"**Feedback dismissed** by citizens ({against_votes} 👎). No effect applied."])
        run(["gh", "issue", "edit", str(number), "--repo", REPO,
             "--add-label", "rejected", "--remove-label", "feedback"])
        run(["gh", "issue", "close", str(number), "--repo", REPO])
        return False

    effect_data = parse_effect(body)
    active_event_now = load_active_event()
    effect_data = apply_crisis_multiplier(effect_data, active_event_now)
    if effect_data and effect_data.get("type") == "policy":
        changes = effect_data.get("changes", {})
        state = read_state()
        for metric, delta in changes.items():
            if metric in POLICY_METRICS:
                state[metric] = max(0, min(100, state.get(metric, 0) + int(delta)))
            elif metric in ("stability", "pollution"):
                state[metric] = max(0, min(100, state.get(metric, 0) + int(delta)))
        write_state(state)
        run_world_engine(None)

        changes_str = ", ".join(
            f"{k} {'+' if int(v) > 0 else ''}{v}" for k, v in changes.items()
        )
        print(f"  Feedback #{number}: APPLIED ({changes_str})")
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", f"**Citizen feedback acknowledged.** Effects applied: {changes_str}"])
    else:
        print(f"  Feedback #{number}: APPLIED (no mechanical effect)")
        run(["gh", "issue", "comment", str(number), "--repo", REPO,
             "--body", "**Citizen feedback noted.** The world has taken notice."])

    run(["gh", "issue", "edit", str(number), "--repo", REPO,
         "--add-label", "applied", "--remove-label", "feedback"])
    run(["gh", "issue", "close", str(number), "--repo", REPO])
    return True


def _ensure_labels():
    labels = [
        ("ai-proposal",    "0075ca", "AI-generated proposal"),
        ("applied",        "0e8a16", "Feedback or effect applied"),
        ("citizen-voices", "d93f0b", "Daily citizen diary"),
        ("dispatch",       "e4e669", "World Chronicle dispatch"),
        ("event",          "6f42c1", "Active world event"),
        ("feedback",       "fbca04", "Citizen feedback"),
        ("passed",         "0e8a16", "Proposal passed and enacted"),
        ("rejected",       "d73a4a", "Proposal rejected"),
    ]
    for name, color, desc in labels:
        run(["gh", "label", "create", name, "--repo", REPO,
             "--color", color, "--description", desc, "--force"])


def save_proposals_json():
    issues = gh_json([
        "api", f"repos/{REPO}/issues?labels=proposal&state=open&per_page=10",
    ])
    minimal = [
        {
            "number": i["number"],
            "title": i["title"],
            "html_url": i["html_url"],
            "created_at": i["created_at"],
            "reactions": {
                "+1": (i.get("reactions") or {}).get("+1", 0),
                "-1": (i.get("reactions") or {}).get("-1", 0),
            },
        }
        for i in (issues if isinstance(issues, list) else [])
    ]
    Path("world/proposals.json").write_text(
        json.dumps(minimal, indent=2), encoding="utf-8"
    )
