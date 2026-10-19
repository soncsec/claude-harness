#!/usr/bin/env python3
"""SessionStart hook: remind the operator of what is waiting on them.

Lists open GitHub issues labeled `needs-human` and open pull requests that
still need a review. Its output is added to Claude's context, so every new
session starts by reminding the operator.

Prints nothing (and never fails the session) when the repo has no GitHub
remote, `gh` is missing or not logged in, or GitHub is slow.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime

GH_TIMEOUT_SECONDS = 8
MAX_ITEMS = 10


def gh_json(args: list[str]) -> list[dict]:
    gh = shutil.which("gh")
    if not gh:
        return []
    try:
        result = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [gh, *args],
            capture_output=True,
            text=True,
            timeout=GH_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    try:
        items = json.loads(result.stdout or "[]")
    except ValueError:
        return []
    return items if isinstance(items, list) else []


def age(created_at: str) -> str:
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return ""
    days = (datetime.now(UTC) - created).days
    if days <= 0:
        return "opened today"
    return f"waiting {days} day{'s' if days != 1 else ''}"


def main() -> int:
    issues = gh_json([
        "issue", "list", "--label", "needs-human", "--state", "open",
        "--limit", str(MAX_ITEMS), "--json", "number,title,url,createdAt",
    ])  # fmt: skip
    prs = gh_json([
        "pr", "list", "--state", "open", "--limit", str(MAX_ITEMS),
        "--json", "number,title,url,createdAt,isDraft,reviewDecision",
    ])  # fmt: skip
    prs = [p for p in prs if not p.get("isDraft") and p.get("reviewDecision") != "APPROVED"]

    if not issues and not prs:
        return 0

    lines = ["Operator reminders from GitHub (mention these in your first reply):"]
    for item in issues:
        lines.append(
            f"- needs-human #{item.get('number')}: {item.get('title')} "
            f"({age(item.get('createdAt', ''))}) {item.get('url')}"
        )
    for item in prs:
        lines.append(
            f"- PR #{item.get('number')} awaiting review: {item.get('title')} "
            f"({age(item.get('createdAt', ''))}) {item.get('url')}"
        )
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 - reminders are best effort
        sys.exit(0)
