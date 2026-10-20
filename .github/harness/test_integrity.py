#!/usr/bin/env python3
"""Flag pull requests that weaken tests, unless a maintainer has approved them.

Compares the pull request with its target branch and reports:

- deleted test files
- test files that lose assertions
- new skip/xfail markers
- new suppressions: # noqa, # nosec, # nosemgrep, # type: ignore, # pragma: no cover
- new test-config tricks: --deselect, --ignore, collect_ignore, -p no:

Any finding fails the check, unless the pull request has an approving review,
on its latest commit, from the repo owner or a user named in CODEOWNERS. Approving
re-runs the check (the workflow listens for reviews), which then passes.

CI runs this file from the target branch, never from the pull request, so a
pull request cannot change the rules that judge it.

Standard library only. Installed by claude-harness (source:
core/github/harness/test_integrity.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

TEST_FILE = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|conftest\.py$)")
SKIPS = re.compile(
    r"@pytest\.mark\.(skip|skipif|xfail)\b|\bpytest\.(skip|xfail)\(|@unittest\.skip"
    r"|@unittest\.expectedFailure"
)
SUPPRESSIONS = re.compile(
    r"#\s*(noqa\b|nosec\b|nosemgrep\b|type:\s*ignore\b|pragma:\s*no\s*cover\b)|\bNOSONAR\b"
)
CONFIG_FILES = re.compile(r"(^|/)(pyproject\.toml|setup\.cfg|pytest\.ini|tox\.ini|conftest\.py)$")
CONFIG_TRICKS = re.compile(r"--deselect\b|--ignore\b|\bcollect_ignore\b|-p\s*no:")
ASSERTS = re.compile(r"^\s*(assert\b|self\.assert\w*\(|with\s+pytest\.raises\b)")


@dataclass
class Finding:
    path: str
    message: str

    def annotation(self) -> str:
        return f"::error file={self.path}::{self.message}"


def git(*args: str) -> str:
    # Arguments come from this script, not from the pull request's content.
    command = ["git", *args]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout  # noqa: S603, S607


def changed_lines(base: str, head: str) -> dict[str, tuple[list[str], list[str]]]:
    """For each changed file: (added lines, removed lines), from the merge base."""
    diff = git("diff", "--unified=0", "--no-color", "--no-renames", f"{base}...{head}")
    files: dict[str, tuple[list[str], list[str]]] = {}
    current: tuple[list[str], list[str]] | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path = line.split(" b/", 1)[-1]
            current = files.setdefault(path, ([], []))
        elif current is None or line.startswith(("+++", "---")):
            continue
        elif line.startswith("+"):
            current[0].append(line[1:])
        elif line.startswith("-"):
            current[1].append(line[1:])
    return files


def deleted_files(base: str, head: str) -> list[str]:
    out = git("diff", "--name-only", "--diff-filter=D", "--no-renames", f"{base}...{head}")
    return [path for path in out.splitlines() if path]


def find_problems(base: str, head: str) -> list[Finding]:
    findings = [
        Finding(path, "test file deleted")
        for path in deleted_files(base, head)
        if TEST_FILE.search(path)
    ]
    for path, (added, removed) in changed_lines(base, head).items():
        if not path.endswith((".py", ".toml", ".cfg", ".ini")):
            continue
        for line in added:
            if path.endswith(".py") and SKIPS.search(line):
                findings.append(Finding(path, f"new skip or xfail: {line.strip()}"))
            if path.endswith(".py") and SUPPRESSIONS.search(line):
                findings.append(Finding(path, f"new suppression: {line.strip()}"))
            if CONFIG_FILES.search(path) and CONFIG_TRICKS.search(line):
                findings.append(Finding(path, f"tests excluded by config: {line.strip()}"))
        if TEST_FILE.search(path) and path.endswith(".py"):
            lost = sum(bool(ASSERTS.match(x)) for x in removed)
            gained = sum(bool(ASSERTS.match(x)) for x in added)
            if lost > gained:
                findings.append(
                    Finding(path, f"{lost - gained} assertion(s) removed from a test file")
                )
    return findings


def api(path: str) -> object:
    base = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    request = urllib.request.Request(f"{base}{path}")  # noqa: S310 - fixed https base
    request.add_header("Accept", "application/vnd.github+json")
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.load(response)


def maintainers(repo: str, base: str) -> set[str]:
    """Who may approve test changes: the repo owner and the users named in the
    target branch's CODEOWNERS (teams are not expanded). The pull request cannot
    change either."""
    names = {repo.split("/")[0].lower()}
    try:
        codeowners = git("show", f"{base}:.github/CODEOWNERS")
    except subprocess.CalledProcessError:
        codeowners = ""
    names.update(login.lower() for login in re.findall(r"@([A-Za-z0-9-]+)(?![\w/-])", codeowners))
    return names


def maintainer_approval(repo: str, pr: int, head_sha: str, allowed: set[str]) -> str | None:
    """Login of a maintainer whose latest review approves the current head commit."""
    latest: dict[str, dict] = {}
    for review in api(f"/repos/{repo}/pulls/{pr}/reviews?per_page=100"):
        login = ((review.get("user") or {}).get("login") or "").lower()
        if login and review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[login] = review
    for login, review in latest.items():
        if (
            login in allowed
            and review["state"] == "APPROVED"
            and review.get("commit_id") == head_sha
        ):
            return login
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True, help="target branch commit or ref")
    parser.add_argument("--head", default="HEAD", help="pull request commit or ref")
    parser.add_argument("--repo", help="owner/name, to look for a maintainer's approval")
    parser.add_argument("--pr", type=int, help="pull request number")
    parser.add_argument("--author", help="login of the pull request's author")
    args = parser.parse_args()

    findings = find_problems(args.base, args.head)
    if not findings:
        print("No weakened tests found.")
        return 0

    approver = None
    if args.repo:
        allowed = maintainers(args.repo, args.base)
        if args.author and args.author.lower() in allowed:
            # A maintainer's own pull request: GitHub never lets authors approve
            # their own work, and the approval exists to check agents, not them.
            approver = f"{args.author} (the author)"
        elif args.pr:
            head_sha = git("rev-parse", args.head).strip()
            approver = maintainer_approval(args.repo, args.pr, head_sha, allowed)
    for finding in findings:
        print(
            f"::notice file={finding.path}::{finding.message}" if approver else finding.annotation()
        )
    if approver:
        print(f"{len(findings)} test change(s) found; approved by maintainer {approver}.")
        return 0
    print(
        f"{len(findings)} change(s) weaken tests. They need a maintainer's approval: "
        "review the pull request and approve it if the changes are intended."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
