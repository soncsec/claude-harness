"""next_issue.py: picks the next issue an agent may work on, trusting only known authors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "core" / "claude" / "tools" / "next_issue.py"
AGENT = "acme-claude-agent[bot]"

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
if isinstance(response, dict) and "exit" in response:
    sys.stderr.write(response.get("stderr", "") + "\\n")
    sys.exit(response["exit"])
print(json.dumps(response))
"""


def issue(
    number: int,
    *,
    assoc: str = "OWNER",
    login: str = "owner",
    user_type: str = "User",
    labels: tuple[str, ...] = (),
    milestone: dict | None = None,
    created: str | None = None,
) -> dict:
    return {
        "number": number,
        "title": f"Issue {number}",
        "html_url": f"https://github.com/acme/app/issues/{number}",
        "author_association": assoc,
        "user": {"login": login, "type": user_type},
        "labels": [{"name": name} for name in labels],
        "milestone": milestone,
        "created_at": created or f"2026-01-{number:02d}T00:00:00Z",
    }


def pull(number: int, body: str) -> dict:
    return {**issue(number), "pull_request": {"url": "..."}, "body": body}


def bot(number: int, login: str = AGENT, **kwargs) -> dict:
    return issue(number, assoc="CONTRIBUTOR", login=login, user_type="Bot", **kwargs)


def outsider(number: int, **kwargs) -> dict:
    return issue(number, assoc="NONE", login="stranger", **kwargs)


@pytest.fixture
def run(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    state = tmp_path / "gh-state"
    state.mkdir()

    def _run(
        *pages, args: tuple[str, ...] = (), agent: str | None = AGENT, path: str | None = None
    ):
        (state / "responses.json").write_text(json.dumps(list(pages)))
        env = {k: v for k, v in os.environ.items() if k != "GIT_AUTHOR_NAME"}
        env.update(
            FAKE_GH_DIR=str(state), PATH=path if path is not None else f"{bin_dir}:/usr/bin:/bin"
        )
        if agent is not None:
            env["GIT_AUTHOR_NAME"] = agent
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            check=False,
        )
        log = (state / "log").read_text() if (state / "log").exists() else ""
        return proc, log

    return _run


# --- Who may hand the agent work -------------------------------------------


@pytest.mark.parametrize("assoc", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_issues_from_people_with_access_are_taken_without_a_label(run, assoc):
    proc, _ = run([issue(4, assoc=assoc)])
    assert proc.returncode == 0
    assert proc.stdout.startswith("next: #4 Issue 4 (https://github.com/acme/app/issues/4)")


def test_issues_the_projects_own_agent_opened_are_taken(run):
    proc, _ = run([bot(7)])
    assert proc.stdout.startswith("next: #7 ")


@pytest.mark.parametrize("assoc", ["NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "FIRST_TIMER"])
def test_issues_from_anyone_else_wait_for_the_ready_label(run, assoc):
    proc, _ = run([issue(3, assoc=assoc, login="stranger")])
    assert proc.returncode == 0
    assert proc.stdout.startswith("none: ")
    assert "need the `ready` label first: #3" in proc.stdout


def test_the_ready_label_lets_an_outsiders_issue_through(run):
    proc, _ = run([outsider(3, labels=("ready", "bug"))])
    assert proc.stdout.startswith("next: #3 ")


def test_another_apps_bot_is_not_trusted(run):
    proc, _ = run([bot(5, login="someone-elses-app[bot]")])
    assert proc.stdout.startswith("none: ")


def test_a_person_cannot_pass_as_the_agent(run):
    proc, _ = run([issue(5, assoc="NONE", login=AGENT, user_type="User")])
    assert proc.stdout.startswith("none: ")


def test_without_an_agent_identity_only_people_with_access_count(run):
    proc, _ = run([bot(2), issue(9)], agent=None)
    assert proc.stdout.startswith("next: #9 ")


def test_an_agent_name_that_is_not_a_bot_login_is_ignored(run):
    proc, _ = run([issue(2, assoc="NONE", login="mallory", user_type="Bot")], agent="mallory")
    assert proc.stdout.startswith("none: ")


def test_the_agent_login_can_be_given_explicitly(run):
    proc, _ = run([bot(6, login="other-agent[bot]")], args=("--agent", "other-agent[bot]"))
    assert proc.stdout.startswith("next: #6 ")


# --- What is never taken ---------------------------------------------------


def test_operator_steps_are_never_taken(run):
    proc, _ = run([issue(1, labels=("needs-human",)), bot(2, labels=("needs-human", "ready"))])
    assert proc.stdout == "none: no issue is open to agents\n"


def test_pull_requests_and_issues_they_already_close_are_skipped(run):
    items = [pull(10, "Plan...\n\nCloses #1"), pull(11, "fixes: #2"), issue(1), issue(2), issue(3)]
    proc, _ = run(items)
    assert proc.stdout.startswith("next: #3 ")


def test_a_mention_that_does_not_close_the_issue_does_not_claim_it(run):
    proc, _ = run([pull(10, "Follows up on #1"), issue(1)])
    assert proc.stdout.startswith("next: #1 ")


def test_nothing_open_at_all(run):
    proc, _ = run([])
    assert proc.returncode == 0
    assert proc.stdout == "none: no issue is open to agents\n"


# --- Order -----------------------------------------------------------------

M1 = {"number": 1, "title": "M1", "due_on": "2026-03-01T00:00:00Z"}
M2 = {"number": 2, "title": "M2", "due_on": "2026-06-01T00:00:00Z"}
M3 = {"number": 3, "title": "M3", "due_on": None}


def test_the_earliest_milestone_comes_first_then_the_oldest_issue(run):
    items = [
        issue(1),
        issue(2, milestone=M3),
        issue(3, milestone=M2),
        issue(5, milestone=M1),
        issue(4, milestone=M1),
    ]
    proc, _ = run(items)
    assert proc.stdout.startswith("next: #4 ")


def test_issues_without_a_milestone_come_after_every_milestone(run):
    proc, _ = run([issue(1), issue(8, milestone=M3)])
    assert proc.stdout.startswith("next: #8 ")


# --- Talking to GitHub -----------------------------------------------------


def test_it_asks_gh_for_open_issues_of_the_current_repo(run):
    _, log = run([issue(1)])
    assert log == "api repos/{owner}/{repo}/issues?state=open&per_page=100&page=1\n"


def test_it_reads_every_page(run):
    first = [pull(100 + n, "") for n in range(100)]
    proc, log = run(first, [issue(7)])
    assert proc.stdout.startswith("next: #7 ")
    assert log.splitlines()[-1].endswith("&page=2")


def test_a_gh_failure_is_an_error(run):
    proc, _ = run({"exit": 1, "stderr": "HTTP 401: Bad credentials"})
    assert proc.returncode == 1
    assert proc.stdout == "error: could not list issues: HTTP 401: Bad credentials\n"


def test_a_missing_gh_is_an_error(run, tmp_path):
    proc, _ = run([], path=str(tmp_path / "empty"))
    assert proc.returncode == 1
    assert proc.stdout.startswith("error: gh is not installed")
