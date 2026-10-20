"""watch_pr.py: waits for a pull request's checks or merge, then prints one line."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "core" / "claude" / "tools" / "watch_pr.py"

# A fake gh: each call prints the next response from a JSON list (the last repeats)
# and logs its arguments.
FAKE_GH = """#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_GH_DIR"]
with open(os.path.join(state, "log"), "a") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
responses = json.load(open(os.path.join(state, "responses.json")))
count_file = os.path.join(state, "count")
count = int(open(count_file).read()) if os.path.exists(count_file) else 0
open(count_file, "w").write(str(count + 1))
response = responses[min(count, len(responses) - 1)]
if "exit" in response:
    sys.stderr.write(response.get("stderr", "") + "\\n")
    sys.exit(response["exit"])
print(json.dumps(response))
"""

URL = "https://github.com/owner/app/pull/12"


def run_check(name: str, status: str = "COMPLETED", conclusion: str = "SUCCESS") -> dict:
    return {"__typename": "CheckRun", "name": name, "status": status, "conclusion": conclusion}


def pr(*checks: dict, state: str = "OPEN", merge_state: str = "CLEAN") -> dict:
    return {
        "state": state,
        "url": URL,
        "mergeStateStatus": merge_state,
        "statusCheckRollup": list(checks),
    }


@pytest.fixture
def fake_gh(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    state = tmp_path / "gh-state"
    state.mkdir()

    def _run(responses: list[dict], *args: str, path: str | None = None):
        (state / "responses.json").write_text(json.dumps(responses))
        env = {
            "PATH": path if path is not None else f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_DIR": str(state),
        }
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args, "--interval", "0"],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            check=False,
        )
        log = (state / "log").read_text() if (state / "log").exists() else ""
        return proc, log

    return _run


def test_waits_for_pending_checks_then_reports_passed(fake_gh):
    responses = [
        pr(run_check("check", status="IN_PROGRESS", conclusion=""), run_check("secrets")),
        pr(run_check("check"), run_check("secrets")),
    ]
    proc, log = fake_gh(responses, "12")
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"passed: all 2 checks passed on {URL} (merge state: CLEAN)"
    assert "pr view 12 --json state,url,mergeStateStatus,statusCheckRollup" in log
    assert len(log.splitlines()) == 3  # pending, done, done again to be sure


def test_a_check_that_appears_late_is_waited_for(fake_gh):
    responses = [
        pr(run_check("check")),
        pr(run_check("check"), run_check("sast", status="QUEUED", conclusion="")),
        pr(run_check("check"), run_check("sast")),
    ]
    proc, _ = fake_gh(responses, "12")
    assert proc.stdout.startswith("passed: all 2 checks passed")


def test_failed_checks_are_named(fake_gh):
    checks = (
        run_check("check", conclusion="FAILURE"),
        run_check("secrets"),
        run_check("sast", conclusion="TIMED_OUT"),
    )
    responses = [pr(*checks)]
    proc, _ = fake_gh(responses, "12")
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"failed: check, sast on {URL}"


def test_commit_statuses_count_as_checks(fake_gh):
    responses = [pr({"__typename": "StatusContext", "context": "ci/legacy", "state": "FAILURE"})]
    proc, _ = fake_gh(responses, "12")
    assert proc.stdout.startswith("failed: ci/legacy")


def test_skipped_and_neutral_checks_pass(fake_gh):
    responses = [pr(run_check("a", conclusion="SKIPPED"), run_check("b", conclusion="NEUTRAL"))]
    proc, _ = fake_gh(responses, "12")
    assert proc.stdout.startswith("passed:")


def test_merge_ends_the_watch_in_either_mode(fake_gh):
    proc, _ = fake_gh([pr(state="MERGED")], "12")
    assert proc.stdout.strip() == f"merged: {URL}"


def test_until_merged_ignores_checks(fake_gh):
    failing = pr(run_check("check", conclusion="FAILURE"))
    responses = [failing, failing, failing, pr(state="MERGED")]
    proc, log = fake_gh(responses, "12", "--until", "merged")
    assert proc.stdout.strip() == f"merged: {URL}"
    assert len(log.splitlines()) == 4


def test_closed_without_merging(fake_gh):
    proc, _ = fake_gh([pr(state="CLOSED")], "12", "--until", "merged")
    assert proc.stdout.strip() == f"closed: {URL} was closed without merging"


def test_no_checks_at_all_is_reported(fake_gh):
    proc, _ = fake_gh([pr()], "12", "--no-checks-minutes", "0")
    assert proc.stdout.startswith(f"no-checks: no CI checks on {URL}")


def test_timeout_says_to_watch_again(fake_gh):
    pending = pr(run_check("check", status="IN_PROGRESS", conclusion=""))
    proc, _ = fake_gh([pending], "12", "--timeout", "0")
    assert proc.returncode == 0
    assert proc.stdout.startswith("timeout:")
    assert "run it again" in proc.stdout


def test_one_gh_failure_is_retried(fake_gh):
    responses = [{"exit": 1, "stderr": "HTTP 502"}, pr(state="MERGED")]
    proc, _ = fake_gh(responses, "12")
    assert proc.stdout.startswith("merged:")


def test_only_failures_in_a_row_count(fake_gh):
    error = {"exit": 1, "stderr": "HTTP 502"}
    pending = pr(run_check("check", status="IN_PROGRESS", conclusion=""))
    responses = [error] * 9 + [pending] + [error] * 9 + [pr(state="MERGED")]
    proc, _ = fake_gh(responses, "12")
    assert proc.stdout.startswith("merged:")


def test_gh_that_keeps_failing_gives_up(fake_gh):
    proc, log = fake_gh([{"exit": 1, "stderr": "HTTP 401: Bad credentials"}], "12")
    assert proc.returncode == 1
    assert proc.stdout.startswith("error: gh pr view 12 failed 10 times in a row")
    assert "Bad credentials" in proc.stdout
    assert len(log.splitlines()) == 10


def test_missing_gh_is_reported_at_once(fake_gh, tmp_path):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    proc, _ = fake_gh([pr()], "12", path=str(empty))
    assert proc.returncode == 1
    assert proc.stdout.strip() == "error: gh is not installed or not on PATH"


@pytest.mark.parametrize("number", ["--repo=evil/repo", "12;ls", "abc", "²"])
def test_only_a_plain_number_is_accepted(fake_gh, number):
    proc, log = fake_gh([pr(state="MERGED")], number)
    assert proc.returncode == 2
    assert log == ""
