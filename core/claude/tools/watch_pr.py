#!/usr/bin/env python3
"""Wait until a pull request needs the agent again, then print one line and exit.

Run it in the background after opening a pull request (Bash with
`run_in_background`), so the session wakes up by itself instead of sitting idle:

    python3 .claude/tools/watch_pr.py 12                 # until the checks finish
    python3 .claude/tools/watch_pr.py 12 --until merged  # until the operator merges

The line it prints starts with one of:

    passed:     every check on the latest commit passed
    failed:     at least one check failed (their names follow)
    merged:     the operator merged it
    closed:     it was closed without merging
    no-checks:  no checks appeared (CI may be missing or broken)
    timeout:    nothing happened in time; run it again to keep watching
    error:      gh kept failing

Standard library only. It calls `gh pr view`, which uses the agent's token.
Installed by claude-harness (source: core/claude/tools/watch_pr.py).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time

PENDING, PASS, FAIL = "pending", "pass", "fail"
PASSING_CONCLUSIONS = {"SUCCESS", "NEUTRAL", "SKIPPED"}
PENDING_STATES = {"PENDING", "EXPECTED", ""}
MAX_ERRORS = 10  # gh failures in a row before giving up


def check_result(check: dict) -> tuple[str, str]:
    """(name, pending|pass|fail) for one entry of gh's statusCheckRollup."""
    if check.get("__typename") == "StatusContext" or "context" in check:
        name = check.get("context") or "status"
        state = str(check.get("state") or "").upper()
        if state in PENDING_STATES:
            return name, PENDING
        return name, PASS if state == "SUCCESS" else FAIL
    name = check.get("name") or check.get("workflowName") or "check"
    if str(check.get("status") or "").upper() != "COMPLETED":
        return name, PENDING
    conclusion = str(check.get("conclusion") or "").upper()
    return name, PASS if conclusion in PASSING_CONCLUSIONS else FAIL


def pr_status(gh: str, number: str) -> dict:
    fields = "state,url,mergeStateStatus,statusCheckRollup"
    proc = subprocess.run(  # noqa: S603 - fixed argument list, no shell
        [gh, "pr", "view", number, "--json", fields],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if proc.returncode != 0:
        lines = proc.stderr.strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"gh exited with {proc.returncode}")
    return json.loads(proc.stdout)


def checks_line(pr: dict, url: str) -> str | None:
    """The line to print once every check has finished, else None."""
    results = sorted(check_result(c) for c in pr.get("statusCheckRollup") or [])
    if not results or any(state == PENDING for _, state in results):
        return None
    failed = [name for name, state in results if state == FAIL]
    if failed:
        return f"failed: {', '.join(failed)} on {url}"
    merge_state = pr.get("mergeStateStatus") or "UNKNOWN"
    return f"passed: all {len(results)} checks passed on {url} (merge state: {merge_state})"


def watch(
    number: str, until: str, interval: float, timeout_min: float, no_checks_min: float
) -> str:
    gh = shutil.which("gh")
    if not gh:
        return "error: gh is not installed or not on PATH"
    start = time.monotonic()
    errors = 0
    last_line = None  # a result must be seen twice in a row: late checks can still appear
    while True:
        try:
            pr = pr_status(gh, number)
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            errors += 1
            if errors >= MAX_ERRORS:
                return f"error: gh pr view {number} failed {errors} times in a row: {exc}"
        else:
            errors = 0
            url = pr.get("url") or f"#{number}"
            state = pr.get("state")
            if state == "MERGED":
                return f"merged: {url}"
            if state == "CLOSED":
                return f"closed: {url} was closed without merging"
            if until == "checks":
                line = checks_line(pr, url)
                if line and line == last_line:
                    return line
                last_line = line
                waited_min = (time.monotonic() - start) / 60
                if not pr.get("statusCheckRollup") and waited_min >= no_checks_min:
                    return f"no-checks: no CI checks on {url} after {no_checks_min:g} minutes"
        if (time.monotonic() - start) / 60 >= timeout_min:
            return (
                f"timeout: no change on #{number} in {timeout_min:g} minutes;"
                " run it again to keep watching"
            )
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("number", help="the pull request number")
    parser.add_argument(
        "--until",
        choices=("checks", "merged"),
        default="checks",
        help="checks (default): wait for every check; merged: wait for the merge",
    )
    parser.add_argument(
        "--interval", type=float, default=30, help="seconds between polls (default 30)"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="minutes before giving up (default 60 for checks, 1440 for merged)",
    )
    parser.add_argument(
        "--no-checks-minutes",
        type=float,
        default=10,
        help="minutes to wait for the first check to appear (default 10)",
    )
    args = parser.parse_args(argv)
    if not (args.number.isascii() and args.number.isdigit()):
        parser.error("the pull request number must be digits only, like 12")
    if args.interval < 0:
        parser.error("--interval must not be negative")
    timeout = args.timeout if args.timeout is not None else (60 if args.until == "checks" else 1440)
    line = watch(args.number, args.until, args.interval, timeout, args.no_checks_minutes)
    print(line, flush=True)
    return 1 if line.startswith("error:") else 0


if __name__ == "__main__":
    sys.exit(main())
