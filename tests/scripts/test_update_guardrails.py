"""update-guardrails.sh, end to end and offline.

A local bare repository stands in for GitHub and a stand-in `gh` records what
it was asked to do, as in test_new_project.py.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "update-guardrails.sh"
INSTALL = REPO / "scripts" / "install-harness.sh"
REMOTE = "https://github.com/owner/repo.git"

# FAKE_GH_CHECKS: "pass" (default), "fail", or "none" (no CI configured).
FAKE_GH = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
case "$*" in
  *permissions.admin*) echo true ;;
  *defaultBranchRef*)  echo main ;;
  "api repos/owner/repo/rulesets") echo '[]' ;;
  "pr create"*)        echo "https://github.com/owner/repo/pull/7" ;;
  "pr checks"*)
    case "${{FAKE_GH_CHECKS:-pass}}" in
      pass) echo "All checks were successful" ;;
      fail) echo "X sast  fail" ; exit 1 ;;
      none) echo "no checks reported on the 'x' branch" ; exit 1 ;;
    esac ;;
esac
exit 0
"""


def git(*args: str, env: dict | None = None) -> str:
    return subprocess.run(
        ["git", *args], check=True, capture_output=True, text=True, env=env
    ).stdout


@pytest.fixture
def setup(tmp_path: Path) -> dict:
    bare = tmp_path / "github" / "repo.git"
    bare.parent.mkdir()
    git("init", "-q", "--bare", "-b", "main", str(bare))

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.format(log=tmp_path / "gh.log"))
    gh.chmod(0o755)
    no_agent = tmp_path / "no-agent-config"
    no_agent.mkdir()
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{bare}.insteadOf",
        "GIT_CONFIG_VALUE_0": REMOTE,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "CLAUDE_HARNESS_CONFIG_DIR": str(no_agent),
        "UPDATE_GUARDRAILS_CHECK_TRIES": "1",
        "UPDATE_GUARDRAILS_CHECK_SLEEP": "0",
    }

    # A project with the current harness installed and pushed.
    project = tmp_path / "project"
    git("init", "-q", "-b", "main", str(project))
    git("-C", str(project), "remote", "add", "origin", REMOTE)
    subprocess.run([str(INSTALL), "--yes", str(project)], env=env, check=True, capture_output=True)
    git("-C", str(project), "add", ".", env=env)
    git("-C", str(project), "commit", "-q", "-m", "install", env=env)
    git("-C", str(project), "push", "-q", "-u", "origin", "main", env=env)
    return {"env": env, "bare": bare, "project": project, "log": tmp_path / "gh.log"}


def make_outdated(setup: dict) -> None:
    """Simulate an older harness version on main: a changed hook, a missing workflow."""
    project, env = setup["project"], setup["env"]
    hook = project / ".claude" / "hooks" / "guard_bash.py"
    hook.write_text(hook.read_text() + "\n# old version\n")
    git("-C", str(project), "rm", "-q", ".github/workflows/security.yml", env=env)
    git("-C", str(project), "commit", "-q", "-am", "older guardrails", env=env)
    git("-C", str(project), "push", "-q", env=env)


def run(setup: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *args, str(setup["project"])],
        capture_output=True,
        text=True,
        env=setup["env"],
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )


def branches(setup: dict) -> list[str]:
    return git("-C", str(setup["bare"]), "branch", "--format=%(refname:short)").split()


def test_outdated_project_gets_a_pr_that_is_merged_after_ci(setup):
    make_outdated(setup)
    result = run(setup, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr

    update = [b for b in branches(setup) if b.startswith("update-guardrails-")]
    assert len(update) == 1
    files = git("-C", str(setup["bare"]), "ls-tree", "-r", "--name-only", update[0]).split()
    assert ".github/workflows/security.yml" in files
    hook = git("-C", str(setup["bare"]), "show", f"{update[0]}:.claude/hooks/guard_bash.py")
    assert "# old version" not in hook

    log = setup["log"].read_text()
    assert "pr create" in log
    assert "pr checks 7" in log
    assert "pr merge 7 --merge --admin --delete-branch" in log
    assert log.index("pr checks") < log.index("pr merge")
    assert "rulesets" in log  # rules re-applied
    assert "Done:" in result.stdout


def test_failing_check_stops_before_merging(setup):
    make_outdated(setup)
    setup["env"]["FAKE_GH_CHECKS"] = "fail"
    result = run(setup, "--yes")
    assert result.returncode != 0
    assert "nothing was merged" in result.stderr
    assert "pr merge" not in setup["log"].read_text()


def test_no_ci_configured_still_merges(setup):
    make_outdated(setup)
    setup["env"]["FAKE_GH_CHECKS"] = "none"
    result = run(setup, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No CI checks were reported" in result.stdout
    assert "pr merge 7" in setup["log"].read_text()


def test_no_merge_stops_after_the_pr(setup):
    make_outdated(setup)
    result = run(setup, "--yes", "--no-merge")
    assert result.returncode == 0, result.stdout + result.stderr
    log = setup["log"].read_text()
    assert "pr create" in log
    assert "pr merge" not in log


def test_up_to_date_project_only_reapplies_rules(setup):
    result = run(setup, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "already up to date" in result.stdout
    log = setup["log"].read_text()
    assert "pr create" not in log
    assert "rulesets" in log


def test_cancelling_leaves_no_trace(setup):
    make_outdated(setup)
    project = setup["project"]
    (project / ".github" / "workflows" / "mine.yml").write_text("name: mine\n")  # untracked, yours
    result = run(setup)  # no --yes and no terminal: the answer is no
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Cancelled" in result.stdout
    status = git("-C", str(project), "status", "--porcelain")
    assert status.strip() == "?? .github/workflows/mine.yml"
    assert not any(b.startswith("update-guardrails-") for b in branches(setup))


def test_old_harness_rules_leave_claude_md_in_the_update_pr(setup):
    # An older install put the harness's rules in CLAUDE.md; the update moves them out.
    project, env = setup["project"], setup["env"]
    legacy = json.loads((REPO / "scripts" / "lib" / "claude-md-legacy.json").read_text())
    workflow = legacy["sections"]["## Workflow"][0]
    claude_md = project / "CLAUDE.md"
    claude_md.write_text(f"# repo\n\n{legacy['comments'][0]}\n\n{workflow}\n## Ours\n\nKeep.\n")
    git("-C", str(project), "commit", "-q", "-am", "older install", env=env)
    git("-C", str(project), "push", "-q", env=env)

    result = run(setup, "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    update = [b for b in branches(setup) if b.startswith("update-guardrails-")][0]
    committed = git("-C", str(setup["bare"]), "show", f"{update}:CLAUDE.md")
    assert "## Workflow" not in committed
    assert "## Ours\n\nKeep.\n" in committed
    files = git("-C", str(setup["bare"]), "ls-tree", "-r", "--name-only", update).split()
    assert ".claude/rules/harness.md" in files
    assert git("-C", str(project), "status", "--porcelain", "--", "CLAUDE.md") == ""


def test_uncommitted_changes_are_refused(setup):
    (setup["project"] / "CLAUDE.md").write_text("edited\n")
    result = run(setup, "--yes")
    assert result.returncode != 0
    assert "uncommitted changes" in result.stderr


def test_dry_run_changes_nothing(setup):
    make_outdated(setup)
    result = run(setup, "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dry run" in result.stdout
    assert git("-C", str(setup["project"]), "status", "--porcelain") == ""
