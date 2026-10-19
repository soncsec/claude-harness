"""protect_files.py: secrets stay unreadable, guardrail files stay unwritable."""

from __future__ import annotations

import os

import pytest


def call(tool: str, path: str, cwd: str | None = None) -> dict:
    key = "path" if tool in ("Grep", "Glob") else "file_path"
    payload = {"tool_name": tool, "tool_input": {key: path}}
    if cwd:
        payload["cwd"] = cwd
    return payload


@pytest.mark.parametrize(
    "tool,path",
    [
        ("Edit", ".claude/settings.json"),
        ("Write", ".claude/hooks/new_hook.py"),
        ("Write", ".github/workflows/ci.yml"),
        ("Edit", ".github/CODEOWNERS"),
        ("Edit", "quality-gates.toml"),
        ("Edit", ".pre-commit-config.yaml"),
        ("Write", ".security/exceptions.yml"),
        ("Edit", ".worktrees/issue-12/.claude/settings.json"),
        ("Edit", "src/../.claude/settings.json"),
        ("NotebookEdit", ".claude/notes.ipynb"),
    ],
)
def test_blocks_writes_to_guardrail_files(run_hook, tool, path):
    result = run_hook("protect_files.py", call(tool, path))
    assert result.denied, f"{tool} {path} should be blocked"
    assert "operator" in result.reason


@pytest.mark.parametrize(
    "tool,path",
    [
        ("Read", ".env"),
        ("Read", "config/.env.production"),
        ("Edit", ".env"),
        ("Read", "certs/server.pem"),
        ("Read", "~/.ssh/id_rsa"),
        ("Grep", "~/.aws"),
        ("Glob", "~/.config/gh"),
        ("Read", "~/.docker/config.json"),
    ],
)
def test_blocks_secret_files(run_hook, tool, path):
    result = run_hook("protect_files.py", call(tool, path))
    assert result.denied, f"{tool} {path} should be blocked"
    assert "Secret" in result.reason


@pytest.mark.parametrize(
    "tool,path",
    [
        ("Read", ".claude/settings.json"),
        ("Grep", ".claude"),
        ("Edit", "src/app.py"),
        ("Write", "README.md"),
        ("Edit", "core/claude/settings.json"),
        ("Read", "env.example"),
        ("Write", "docs/proposals/allow-docker.md"),
    ],
)
def test_allows_normal_files(run_hook, tool, path):
    result = run_hook("protect_files.py", call(tool, path))
    assert not result.denied, f"{tool} {path} should be allowed ({result.reason})"


def test_symlink_to_guardrails_is_followed(run_hook, sandbox_env):
    project = sandbox_env["project"]
    (project / ".claude").mkdir()
    os.symlink(project / ".claude", project / "innocent")
    result = run_hook("protect_files.py", call("Edit", "innocent/settings.json"))
    assert result.denied


def test_absolute_path_is_checked(run_hook, sandbox_env):
    target = sandbox_env["project"] / ".claude" / "settings.json"
    result = run_hook("protect_files.py", call("Write", str(target)))
    assert result.denied


def test_unreadable_input_blocks(run_hook):
    result = run_hook("protect_files.py", "{broken")
    assert result.returncode == 2
