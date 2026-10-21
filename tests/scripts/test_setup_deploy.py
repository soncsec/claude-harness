"""setup-deploy.sh: environments with an approval gate, one deploy key per environment."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "setup-deploy.sh"
WORKFLOW = REPO / "core" / "deploy" / "deploy.yml"

# Fake gh: logs each call with its stdin as JSON lines and answers the reads.
FAKE_GH = r"""#!/usr/bin/env python3
import json, os, sys
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
with open(os.path.join(state, "calls.jsonl"), "a") as log:
    log.write(json.dumps({"args": args, "stdin": stdin}) + "\n")
joined = " ".join(args)
if args[:2] == ["repo", "view"] and "defaultBranchRef" in joined:
    print(os.environ.get("FAKE_DEFAULT_BRANCH", "main"))
elif args[:2] == ["repo", "view"] and "visibility" in joined:
    print(os.environ.get("FAKE_VISIBILITY", "PUBLIC"))
elif args[:2] == ["api", "user"]:
    print(4242)
elif "environments/production" in joined and "PUT" in args and os.environ.get("FAKE_NO_REVIEWERS"):
    sys.stderr.write("HTTP 422: Upgrade to GitHub Pro or make this repository public\n")
    sys.exit(1)
elif "PUT" in args:
    print("{}")
"""

FAKE_KEYSCAN = """#!/usr/bin/env bash
host=${@: -1}
printf '%s %s\\n' "$host" "$(cat "$FAKE_STATE/hostkey.pub" | cut -d' ' -f1,2)"
"""


@pytest.fixture
def project(tmp_path: Path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "ssh-keyscan").write_text(FAKE_KEYSCAN)
    for name in ("gh", "ssh-keyscan"):
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(state / "hostkey")], check=True
    )
    app = tmp_path / "notes-api"
    app.mkdir()
    subprocess.run(["git", "init", "-q", str(app)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(app),
            "remote",
            "add",
            "origin",
            "https://github.com/Owner/Notes_API.git",
        ],
        check=True,
    )
    (app / "Containerfile").write_text("FROM scratch\n")

    class Project:
        def __init__(self):
            self.dir, self.state = app, state

        def setup(self, *args: str, env: dict | None = None, targets: bool = True):
            run_env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "FAKE_STATE": str(state),
                "TMPDIR": str(tmp_path),
                **(env or {}),
            }
            hosts = (
                (
                    "--staging",
                    "deploy@staging.example.com",
                    "--production",
                    "deploy@app.example.com",
                )
                if targets
                else ()
            )
            return subprocess.run(
                [str(SCRIPT), *hosts, *args, str(app)],
                capture_output=True,
                text=True,
                env=run_env,
                timeout=60,
                check=False,
            )

        def calls(self) -> list[dict]:
            path = state / "calls.jsonl"
            return (
                [json.loads(line) for line in path.read_text().splitlines()]
                if path.exists()
                else []
            )

        def secret(self, name: str, env: str) -> str:
            for call in self.calls():
                a = call["args"]
                if a[:3] == ["secret", "set", name] and a[a.index("--env") + 1] == env:
                    return call["stdin"]
            raise AssertionError(f"{name} was never set for {env}")

    return Project()


def environment_body(project, env: str) -> dict:
    for call in project.calls():
        if "PUT" in call["args"] and f"repos/Owner/Notes_API/environments/{env}" in call["args"]:
            return json.loads(call["stdin"])
    raise AssertionError(f"environment {env} never created")


def test_installs_the_workflow_unchanged(project):
    result = project.setup()
    assert result.returncode == 0, result.stderr
    installed = project.dir / ".github" / "workflows" / "deploy.yml"
    assert installed.read_text() == WORKFLOW.read_text()


def test_production_needs_the_operators_approval(project):
    project.setup()
    production = environment_body(project, "production")
    assert production["reviewers"] == [{"type": "User", "id": 4242}]
    # The operator's own merge triggers the deploy; they must still be able to approve it.
    assert production["prevent_self_review"] is False
    assert "reviewers" not in environment_body(project, "staging")


@pytest.mark.parametrize("env", ["staging", "production"])
def test_only_the_default_branch_may_deploy(project, env):
    project.setup()
    assert environment_body(project, env)["deployment_branch_policy"] == {
        "protected_branches": False,
        "custom_branch_policies": True,
    }
    assert any(
        f"repos/Owner/Notes_API/environments/{env}/deployment-branch-policies" in c["args"]
        and "name=main" in c["args"]
        for c in project.calls()
    )


def public_key_of(private_key: str, tmp_path: Path) -> str:
    key_file = tmp_path / "check-key"
    key_file.write_text(private_key)
    key_file.chmod(0o600)
    out = subprocess.run(["ssh-keygen", "-y", "-f", str(key_file)], capture_output=True, text=True)
    return " ".join(out.stdout.split()[:2])


def test_each_environment_gets_its_own_deploy_key(project, tmp_path):
    result = project.setup()
    staging = project.secret("DEPLOY_SSH_KEY", "staging")
    production = project.secret("DEPLOY_SSH_KEY", "production")
    assert "BEGIN OPENSSH PRIVATE KEY" in staging and staging != production
    # The server commands carry the matching public halves.
    staging_part, production_part = result.stdout.split("Next steps")[1].split("production (")
    assert public_key_of(staging, tmp_path) in staging_part
    assert public_key_of(production, tmp_path) in production_part
    assert "PRIVATE KEY" not in result.stdout + result.stderr


def test_host_keys_and_targets_are_stored_per_environment(project):
    project.setup()
    hostkey = " ".join((project.state / "hostkey.pub").read_text().split()[:2])
    assert project.secret("DEPLOY_KNOWN_HOSTS", "staging").startswith(
        f"staging.example.com {hostkey}"
    )
    variables = {
        c["args"][c["args"].index("--env") + 1]: c["args"][c["args"].index("--body") + 1]
        for c in project.calls()
        if c["args"][:3] == ["variable", "set", "DEPLOY_HOST"]
    }
    assert variables == {
        "staging": "deploy@staging.example.com",
        "production": "deploy@app.example.com",
    }


def test_server_commands_name_the_app_and_its_image(project):
    result = project.setup("--port", "8080")
    assert "--app notes-api --image ghcr.io/owner/notes_api --port 8080" in result.stdout


def test_no_deploy_keys_without_an_approval_gate(project):
    result = project.setup(env={"FAKE_NO_REVIEWERS": "1"})
    assert result.returncode == 1
    assert "every merge would reach production" in result.stderr
    assert not any(c["args"][:2] == ["secret", "set"] for c in project.calls())


def test_a_default_branch_other_than_main_is_refused(project):
    result = project.setup(env={"FAKE_DEFAULT_BRANCH": "trunk"})
    assert result.returncode == 1
    assert "'main'" in result.stderr
    assert not (project.dir / ".github").exists()


def test_private_repos_are_told_about_the_pull_token(project):
    result = project.setup(env={"FAKE_VISIBILITY": "PRIVATE"})
    assert "read:packages" in result.stdout and "--pull-token-file" in result.stdout


def test_dry_run_reads_but_never_writes(project):
    result = project.setup("--dry-run")
    assert result.returncode == 0, result.stderr
    assert not (project.dir / ".github").exists()
    writes = [c for c in project.calls() if "PUT" in c["args"] or c["args"][1:2] == ["set"]]
    assert writes == []


@pytest.mark.parametrize(
    "args",
    [
        ("--staging", "staging.example.com", "--production", "deploy@app.example.com"),
        ("--staging", "deploy@staging.example.com", "--production", "deploy@app;id"),
        ("--staging", "deploy@staging.example.com"),
    ],
)
def test_bad_targets_are_refused(project, args):
    result = project.setup(*args, targets=False)
    assert result.returncode != 0
    assert project.calls() == []


def test_needs_a_github_remote(project):
    subprocess.run(["git", "-C", str(project.dir), "remote", "remove", "origin"], check=True)
    result = project.setup()
    assert result.returncode == 1
    assert "no GitHub 'origin' remote" in result.stderr


def test_ssh_tools_are_available():
    assert shutil.which("ssh-keygen"), "ssh-keygen is needed to run these tests"
