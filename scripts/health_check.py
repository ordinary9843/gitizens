#!/usr/bin/env python3
import json
import sys
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import subprocess

REPO = os.environ.get("GITHUB_REPOSITORY", "ordinary9843/gitizens")

def run(cmd, check=True):
    return subprocess.run(cmd, check=check, capture_output=True, text=True).stdout

def gh_json(cmd):
    return json.loads(run(cmd))

def main():
    state_path = Path("world/state.json")
    if not state_path.exists():
        print("No state.json found — skipping check")
        sys.exit(0)

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("Invalid state.json — skipping check")
        sys.exit(0)

    next_tick_raw = state.get("next_tick_at")
    if not next_tick_raw:
        print("No next_tick_at found — skipping check")
        sys.exit(0)

    try:
        next_tick = datetime.fromisoformat(next_tick_raw.replace("Z", "+00:00"))
        if next_tick.tzinfo is None:
            next_tick = next_tick.replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"Invalid next_tick_at format: {next_tick_raw} — skipping check")
        sys.exit(0)

    now = datetime.now(timezone.utc)
    overdue = now - next_tick
    threshold = timedelta(hours=6)

    # Find existing health issues
    try:
        issues = gh_json([
            "issue", "list", "--repo", REPO, "--label", "bug",
            "--state", "open", "--search", "in:title [HEALTH]", "--json", "number,title"
        ])
    except Exception as e:
        print(f"Failed to query issues: {e}")
        issues = []
    
    health_issues = [i for i in issues if str(i.get("title", "")).startswith("[HEALTH]")]

    if overdue > threshold:
        hours = int(overdue.total_seconds() // 3600)
        print(f"ALERT: World is {hours}h overdue (next_tick_at={next_tick_raw})")
        
        if health_issues:
            print(f"Health issue already exists (#{health_issues[0]['number']}). Doing nothing.")
            sys.exit(1)
            
        title = f"[HEALTH] World tick overdue by {hours}h"
        body = (
            f"The world has not ticked since `{next_tick_raw}` "
            f"({hours} hours overdue).\n\n"
            "Possible causes:\n"
            "- Tally workflow push blocked by branch protection\n"
            "- API permission error (check workflow run logs)\n"
            "- Workflow not running (check Actions schedule)\n\n"
            "Check the latest [Tally Votes run](../../actions/workflows/tally-votes.yml) "
            "for errors."
        )
        run(["gh", "issue", "create", "--repo", REPO, "--title", title, "--body", body, "--label", "bug"])
        print(f"Created new health issue: {title}")
        sys.exit(1)
    else:
        print(f"World is healthy (next_tick_at={next_tick_raw}, overdue={overdue})")
        if health_issues:
            for issue in health_issues:
                number = issue["number"]
                print(f"Closing resolved health issue #{number}")
                run(["gh", "issue", "comment", str(number), "--repo", REPO,
                     "--body", "The world has resumed ticking and is now healthy. Closing this alert."])
                run(["gh", "issue", "close", str(number), "--repo", REPO])

if __name__ == "__main__":  # pragma: no cover
    main()
