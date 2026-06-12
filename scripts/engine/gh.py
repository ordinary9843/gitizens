import os
import json
import subprocess
import time

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]
SKIP_TIMING = os.environ.get("SKIP_TIMING_CHECK", "").lower() in ("1", "true", "yes")


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")
    if result.returncode != 0 and result.stderr.strip():
        print(f"  [WARN] {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}: {result.stderr.strip()[:300]}")
    return result.stdout.strip()


def gh_json(cmd: list[str]) -> list | dict:
    out = run(["gh", *cmd])
    return json.loads(out) if out else []


def push_with_retry(max_attempts: int = 3) -> bool:
    for i in range(max_attempts):
        if i > 0:
            time.sleep(5 * i)
        run(["git", "pull", "--rebase", "origin", "master"])
        result = subprocess.run(
            ["git", "push", "origin", "master", "--follow-tags"],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            print("  Pushed.")
            return True
        print(f"  [WARN] Push attempt {i + 1}/{max_attempts} failed: "
              f"{result.stderr.strip()[:200]}")
    print("  [ERROR] All push attempts failed — dispatch will not be published")
    return False


def get_reactions(issue_number: int) -> tuple[int, int, list[str], list[str]]:
    raw = run(["gh", "api", f"repos/{REPO}/issues/{issue_number}/reactions",
               "--paginate", "--jq", ".[] | {login: .user.login, content: .content}"])
    user_votes: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            user_votes[r["login"]] = r["content"]
        except (json.JSONDecodeError, KeyError):
            continue
    for_voters     = sorted(u for u, v in user_votes.items() if v == "+1")
    against_voters = sorted(u for u, v in user_votes.items() if v == "-1")
    return len(for_voters), len(against_voters), for_voters, against_voters
