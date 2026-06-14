#!/usr/bin/env python3
"""
Bootstrap Gitizens world by seeding policy proposals and running tally.
Run once after a world reset. Requires gh auth token.
"""
import os
import subprocess
import time
from pathlib import Path

REPO = "ordinary9843/gitizens"

PROPOSALS = [
    (
        "Fund the National Education Initiative",
        "Allocate public resources to establish foundational education infrastructure. "
        "A literate and educated citizenry is the bedrock of any thriving civilization. "
        "Vote FOR to invest in our people's future.",
        "type: policy\nchanges:\n  education: +20",
    ),
    (
        "Jumpstart Industrial Development",
        "Provide incentives and infrastructure for manufacturing and industrial growth. "
        "Economic output and job creation depend on a strong industrial base. "
        "Vote FOR to build the nation's productive capacity.",
        "type: policy\nchanges:\n  industry: +30",
    ),
    (
        "Launch the Social Welfare Program",
        "Establish baseline social support systems for all citizens, including healthcare "
        "and housing assistance. A stable population requires security and dignity. "
        "Vote FOR to strengthen the social fabric.",
        "type: policy\nchanges:\n  welfare: +30",
    ),
    (
        "Protect Our Natural Environment",
        "Enact regulations limiting industrial emissions and fund reforestation efforts. "
        "Clean air, clean water, and healthy ecosystems are national assets. "
        "Vote FOR to preserve the land for future generations.",
        "type: policy\nchanges:\n  green_policy: +30",
    ),
    (
        "Establish the National Defense Corps",
        "Form a standing defense force to protect the nation's borders and interests. "
        "Sovereignty requires the credible capacity for self-defense. "
        "Vote FOR to ensure national security.",
        "type: policy\nchanges:\n  defense: +30",
    ),
    (
        "Expand Educational Excellence",
        "Build on early education gains with advanced research institutions and "
        "university funding. Raise the nation's academic and scientific capacity. "
        "Vote FOR to take education to the next level.",
        "type: policy\nchanges:\n  education: +30",
    ),
    (
        "Deepen Welfare Commitments",
        "Expand welfare programs to cover housing subsidies, elder care, and child "
        "support services. Long-term social stability requires sustained investment. "
        "Vote FOR to build a true welfare state.",
        "type: policy\nchanges:\n  welfare: +30",
    ),
    (
        "Invest in Nature Conservation",
        "Establish protected zones, renewable energy mandates, and green urban planning "
        "standards. Balance industrial growth with ecological responsibility. "
        "Vote FOR to keep the world livable.",
        "type: policy\nchanges:\n  green_policy: +25",
    ),
]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, encoding="utf-8").stdout.strip()


def create_issue(title, description, effect_yaml):
    body = f"## Description\n\n{description}\n\n## Effect\n\n```yaml\n{effect_yaml}\n```\n"
    tmp = Path("scripts/_seed_body.txt")
    tmp.write_text(body, encoding="utf-8")
    result = subprocess.run(
        ["gh", "issue", "create", "--repo", REPO,
         "--title", f"[PROPOSAL] {title}",
         "--body-file", str(tmp)],
        capture_output=True, encoding="utf-8",
    )
    tmp.unlink(missing_ok=True)
    url = result.stdout.strip()
    number = int(url.split("/")[-1])
    print(f"  Created #{number}: {title}")
    return number


def add_reaction(number):
    subprocess.run(
        ["gh", "api", f"repos/{REPO}/issues/{number}/reactions",
         "-f", "content=+1"],
        capture_output=True,
    )


def add_label(number):
    subprocess.run(
        ["gh", "issue", "edit", str(number), "--repo", REPO,
         "--add-label", "proposal"],
        capture_output=True,
    )


def run_tally():
    env = os.environ.copy()
    env["GITHUB_TOKEN"]      = run(["gh", "auth", "token"])
    env["GITHUB_REPOSITORY"] = REPO
    env["SKIP_TIMING_CHECK"] = "1"
    result = subprocess.run(
        ["python", "scripts/tally_votes.py"],
        env=env, cwd=str(Path(__file__).parent.parent),
    )
    return result.returncode == 0


if __name__ == "__main__":  # pragma: no cover
    print("=== Gitizens World Seed ===")
    issue_numbers = []

    for title, desc, effect in PROPOSALS:
        n = create_issue(title, desc, effect)
        issue_numbers.append(n)
        time.sleep(1)

    print("\nAdding labels and reactions...")
    for n in issue_numbers:
        add_label(n)
        add_reaction(n)
        time.sleep(0.5)

    print("\nRunning tally...")
    run_tally()
    print("\nSeed complete.")
