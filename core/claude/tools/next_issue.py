#!/usr/bin/env python3
"""Pick the next GitHub issue an agent may work on, then print one line and exit.

    python3 .claude/tools/next_issue.py

An issue is open to agents when someone the operator trusts opened it: the
repository's owner, a member or a collaborator, or this project's own agent
(its bot login comes from GIT_AUTHOR_NAME, which `agent-token.sh configure`
sets). An issue from anyone else needs the `ready` label, which only people
with triage rights can add, so a stranger cannot hand the agent work on a
public repo. `needs-human` issues are steps for the operator and are never
taken, and neither is an issue that an open pull request already closes.

The next issue is the oldest one in the earliest open milestone (by due date,
then creation), then the oldest one without a milestone.

The line it prints starts with one of:

    next:   the issue to work on: number, title and URL
    none:   no issue is open to agents (issues that need the `ready` label are listed)
    error:  gh failed

Standard library only. It calls `gh api`, which uses the agent's token.
Installed by claude-harness (source: core/claude/tools/next_issue.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
READY, NEEDS_HUMAN = "ready", "needs-human"
MAX_PAGES = 10  # 1,000 open issues and pull requests is plenty
CLOSING_REF = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.I)


def fetch_open(gh: str) -> list[dict]:
    """Every open issue and pull request (GitHub's issues API returns both)."""
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        proc = subprocess.run(  # noqa: S603 - fixed argument list, no shell
            [gh, "api", f"repos/{{owner}}/{{repo}}/issues?state=open&per_page=100&page={page}"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode != 0:
            lines = proc.stderr.strip().splitlines()
            raise RuntimeError(lines[-1] if lines else f"gh exited with {proc.returncode}")
        batch = json.loads(proc.stdout)
        if not isinstance(batch, list):
            raise ValueError("gh api did not return a list")
        items.extend(batch)
        if len(batch) < 100:
            break
    return items


def labels(item: dict) -> set[str]:
    return {str(label.get("name", "")) for label in item.get("labels") or []}


def trusted_author(item: dict, agent_login: str) -> bool:
    if item.get("author_association") in TRUSTED_ASSOCIATIONS:
        return True
    user = item.get("user") or {}
    # Bot logins end in [bot] and cannot be taken by a person.
    return bool(agent_login) and user.get("type") == "Bot" and user.get("login") == agent_login


def claimed_issues(items: list[dict]) -> set[int]:
    """Issues an open pull request already closes ("Closes #12" in its body)."""
    claimed: set[int] = set()
    for item in items:
        if "pull_request" in item:
            claimed.update(int(n) for n in CLOSING_REF.findall(item.get("body") or ""))
    return claimed


def order_key(item: dict) -> tuple:
    milestone = item.get("milestone")
    if not milestone:
        return (1, "", 0, item.get("created_at") or "", item.get("number", 0))
    due = milestone.get("due_on") or "9999"  # milestones without a due date come last
    return (0, due, milestone.get("number", 0), item.get("created_at") or "", item.get("number", 0))


def pick(items: list[dict], agent_login: str) -> str:
    claimed = claimed_issues(items)
    workable, waiting = [], []
    for item in items:
        if "pull_request" in item or item.get("number") in claimed:
            continue
        names = labels(item)
        if NEEDS_HUMAN in names:
            continue
        if READY in names or trusted_author(item, agent_login):
            workable.append(item)
        else:
            waiting.append(item)
    if workable:
        issue = min(workable, key=order_key)
        return f"next: #{issue['number']} {issue.get('title', '')} ({issue.get('html_url', '')})"
    line = "none: no issue is open to agents"
    if waiting:
        numbers = ", ".join(f"#{i['number']}" for i in sorted(waiting, key=order_key))
        line += f"; opened by others, these need the `{READY}` label first: {numbers}"
    return line


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--agent",
        default=os.environ.get("GIT_AUTHOR_NAME", ""),
        help="the agent's bot login (default: $GIT_AUTHOR_NAME)",
    )
    args = parser.parse_args(argv)
    agent_login = args.agent if args.agent.endswith("[bot]") else ""
    gh = shutil.which("gh")
    if not gh:
        line = "error: gh is not installed or not on PATH"
    else:
        try:
            line = pick(fetch_open(gh), agent_login)
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            line = f"error: could not list issues: {exc}"
    print(line, flush=True)
    return 1 if line.startswith("error:") else 0


if __name__ == "__main__":
    sys.exit(main())
