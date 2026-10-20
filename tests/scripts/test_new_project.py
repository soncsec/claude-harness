"""new-project.sh, end to end and offline.

A local bare repository stands in for GitHub (git's url.insteadOf rewrite sends
https://github.com/owner/repo.git there), and a stand-in `gh` records what it
was asked to do. So the real flow runs: install, CODEOWNERS, first commit and
push, rules.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "new-project.sh"
REMOTE = "https://github.com/owner/repo.git"

# FAKE_GH_MODE: "" (rulesets work), "free" (GitHub Free private repo), "broken".
# FAKE_GH_PLAN: the plan `gh api user` reports for the account "owner".
FAKE_GH = """#!/bin/bash
printf '%s\\n' "$*" >> {log}
case "$*" in
  *permissions.admin*) echo true ;;
  *defaultBranchRef*)  echo main ;;
  "api user --jq"*)    echo "owner ${{FAKE_GH_PLAN:-pro}}" ;;
  "api repos/owner/repo/rulesets")
    case "${{FAKE_GH_MODE:-}}" in
      free)
        msg="Upgrade to GitHub Pro or make this repository public to enable this feature."
        echo "{{\\"message\\":\\"$msg\\",\\"status\\":\\"403\\"}}"
        echo "gh: $msg (HTTP 403)" >&2
        exit 1 ;;
      broken)
        echo "gh: Server Error (HTTP 500)" >&2
        exit 1 ;;
      *) echo '[]' ;;
    esac ;;
esac
exit 0
"""


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def setup(tmp_path: Path) -> dict:
    bare = tmp_path / "github" / "repo.git"
    bare.parent.mkdir()
    git("init", "-q", "--bare", "-b", "main", str(bare))

    project = tmp_path / "project"
    git("init", "-q", "-b", "main", str(project))
    git("-C", str(project), "remote", "add", "origin", REMOTE)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH.format(log=tmp_path / "gh.log"))
    gh.chmod(0o755)
    empty_config = tmp_path / "no-agent-config"
    empty_config.mkdir()

    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        # Send the GitHub URL to the local bare repo.
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.{bare}.insteadOf",
        "GIT_CONFIG_VALUE_0": REMOTE,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "CLAUDE_HARNESS_CONFIG_DIR": str(empty_config),  # no agent app: step 5 skipped
    }
    return {"env": env, "bare": bare, "project": project, "log": tmp_path / "gh.log"}


def run(setup: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=setup["env"],
        stdin=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )


def test_new_empty_repo_end_to_end(setup):
    result = run(setup, "--yes", str(setup["project"]))
    assert result.returncode == 0, result.stdout + result.stderr

    # The first commit reached "GitHub" (the bare repo) on main, with everything in it.
    files = git("-C", str(setup["bare"]), "ls-tree", "-r", "--name-only", "main").split()
    for expected in (
        ".claude/settings.json",
        ".claude/hooks/guard_bash.py",
        "CLAUDE.md",
        ".gitignore",
        ".github/CODEOWNERS",
    ):
        assert expected in files
    codeowners = git("-C", str(setup["bare"]), "show", "main:.github/CODEOWNERS")
    assert "/.claude/" in codeowners and "@owner" in codeowners
    assert "{{OWNER}}" not in codeowners

    # The rules were applied after the first push.
    calls = setup["log"].read_text()
    assert "rulesets" in calls
    assert "actions/permissions/workflow" in calls
    assert "Done in" in result.stdout
    assert "agent identity step" in result.stdout  # skipped: no app configured


def test_repo_with_history_gets_a_pull_request(setup):
    project = setup["project"]
    (project / "README.md").write_text("existing\n")
    git("-C", str(project), "add", "README.md")
    subprocess.run(
        ["git", "-C", str(project), "commit", "-q", "-m", "existing"],
        env=setup["env"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(project), "push", "-q", "origin", "main"], env=setup["env"], check=True
    )

    result = run(setup, "--yes", str(project))
    assert result.returncode == 0, result.stdout + result.stderr
    branches = git("-C", str(setup["bare"]), "branch", "--format=%(refname:short)").split()
    assert "claude-harness-setup" in branches
    main_files = git("-C", str(setup["bare"]), "ls-tree", "-r", "--name-only", "main").split()
    assert ".claude/settings.json" not in main_files  # main untouched: changes wait in a PR
    assert "pr create" in setup["log"].read_text()


def test_free_private_repo_finishes_with_local_protection(setup):
    setup["env"]["FAKE_GH_MODE"] = "free"
    result = run(setup, "--yes", str(setup["project"]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Done in" in result.stdout
    assert "Protection: local guardrails only (GitHub Free" in result.stdout
    assert "GitHub Free has rulesets" in result.stderr
    # Everything except the rulesets was still done.
    files = git("-C", str(setup["bare"]), "ls-tree", "-r", "--name-only", "main").split()
    assert ".github/CODEOWNERS" in files
    assert "actions/permissions/workflow" in setup["log"].read_text()
    assert "-X POST repos/owner/repo/rulesets" not in setup["log"].read_text()


def test_failed_rules_step_does_not_stop_the_setup(setup):
    setup["env"]["FAKE_GH_MODE"] = "broken"
    result = run(setup, "--yes", str(setup["project"]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GitHub rules FAILED" in result.stdout
    assert "Done in" in result.stdout


def test_github_setup_alone_exits_3_on_free_private(setup):
    setup["env"]["FAKE_GH_MODE"] = "free"
    result = subprocess.run(
        [str(REPO / "scripts" / "github-setup.sh"), "owner/repo"],
        cwd=setup["project"],
        capture_output=True,
        text=True,
        env=setup["env"],
        check=False,
    )
    assert result.returncode == 3
    assert "local guardrails only" in result.stdout


def test_create_warns_up_front_on_free_plan(setup, tmp_path):
    setup["env"]["FAKE_GH_PLAN"] = "free"
    setup["env"]["FAKE_GH_MODE"] = "free"
    target = tmp_path / "created"
    result = run(setup, "--yes", "--create", "owner/repo", str(target))
    assert result.returncode == 0, result.stdout + result.stderr
    out = result.stdout
    assert "on GitHub Free, private repos cannot have GitHub rules" in out
    assert out.index("on GitHub Free") < out.index("[1] Create")
    assert "repo create owner/repo --private" in setup["log"].read_text()


def test_dry_run_changes_nothing(setup):
    result = run(setup, "--dry-run", str(setup["project"]))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "new and empty" in result.stdout
    assert "push -q -u origin main" in result.stdout
    assert not (setup["project"] / ".claude").exists()
    assert git("-C", str(setup["bare"]), "branch") == ""


def test_non_github_remote_is_refused(setup):
    git("-C", str(setup["project"]), "remote", "set-url", "origin", "https://gitlab.com/o/r.git")
    result = run(setup, str(setup["project"]))
    assert result.returncode != 0
    assert "GitHub" in result.stderr


def test_missing_directory_suggests_create(setup, tmp_path):
    result = run(setup, str(tmp_path / "nope"))
    assert result.returncode != 0
    assert "--create" in result.stderr


def test_help():
    result = subprocess.run([str(SCRIPT), "--help"], capture_output=True, text=True, check=False)
    assert result.returncode == 0
    assert "--create OWNER/NAME" in result.stdout
