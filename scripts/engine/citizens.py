import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .constants import SIGNATURE_THRESHOLD, COOLDOWN_DAYS, REPRESENTATIVE_DAYS


def format_signatories(for_voters: list[str], against_voters: list[str]) -> str:
    total = len(for_voters) + len(against_voters)
    for_str     = (", ".join(f"@{u}" for u in for_voters)     or "—")
    against_str = (", ".join(f"@{u}" for u in against_voters) or "—")
    if total <= SIGNATURE_THRESHOLD:
        return (
            f"**Voted for:** {for_str}  \n"
            f"**Voted against:** {against_str}"
        )
    return (
        f"<details>\n"
        f"<summary>👥 {total} signatories</summary>\n\n"
        f"**For:** {for_str}  \n"
        f"**Against:** {against_str}\n\n"
        f"</details>"
    )


def track_citizen_activity(for_voters: list[str], against_voters: list[str]):
    path = Path("world/citizens.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    for user in for_voters + against_voters:
        entry = data.setdefault(user, {"total_votes": 0, "total_proposals": 0, "last_active": now_iso})
        entry["total_votes"] += 1
        entry["last_active"] = now_iso
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def track_citizen_proposal(proposer: str):
    path = Path("world/citizens.json")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    entry = data.setdefault(proposer, {"total_votes": 0, "total_proposals": 0, "last_active": now_iso})
    entry["total_proposals"] += 1
    entry["last_active"] = now_iso
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def check_proposal_cooldown(effect_data: dict | None) -> tuple[bool, str]:
    if not effect_data or effect_data.get("type") != "policy":
        return True, ""
    path = Path("world/proposal_cooldowns.json")
    if not path.exists():
        return True, ""
    try:
        cooldowns = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return True, ""
    today = datetime.now(timezone.utc).date()
    for metric in effect_data.get("changes", {}):
        if metric not in cooldowns:
            continue
        try:
            last_date = datetime.fromisoformat(cooldowns[metric]).date()
        except (ValueError, TypeError):
            continue
        if (today - last_date).days < COOLDOWN_DAYS:
            until = (last_date + timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%d")
            return False, f"metric '{metric}' on cooldown until {until}"
    return True, ""


def update_proposal_cooldown(effect_data: dict | None, date: str):
    if not effect_data or effect_data.get("type") != "policy":
        return
    path = Path("world/proposal_cooldowns.json")
    try:
        cooldowns = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (json.JSONDecodeError, OSError):
        cooldowns = {}
    for metric in effect_data.get("changes", {}):
        cooldowns[metric] = date
    path.write_text(json.dumps(cooldowns, indent=2) + "\n", encoding="utf-8")


def select_weekly_representatives():
    reps_path = Path("world/representatives.json")
    reps = json.loads(reps_path.read_text(encoding="utf-8")) if reps_path.exists() else {"selected_at": None}
    if reps.get("selected_at"):
        try:
            last = datetime.fromisoformat(reps["selected_at"]).date()
            if (datetime.now(timezone.utc).date() - last).days < REPRESENTATIVE_DAYS:
                return
        except (ValueError, TypeError):
            pass
    citizens_path = Path("world/citizens.json")
    if not citizens_path.exists():
        return
    try:
        citizens = json.loads(citizens_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    if not citizens:
        return
    top3 = sorted(citizens.items(), key=lambda x: x[1].get("total_votes", 0), reverse=True)[:3]
    representatives = [u for u, _ in top3]
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    next_str = (datetime.now(timezone.utc) + timedelta(days=REPRESENTATIVE_DAYS)).strftime("%Y-%m-%d")
    reps_path.write_text(
        json.dumps({"selected_at": today_str, "next_selection": next_str,
                    "representatives": representatives}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"  Representatives: {representatives}")
