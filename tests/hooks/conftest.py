"""Helpers to run the harness hooks the way Claude Code does: a subprocess fed JSON on stdin."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / "core" / "claude" / "hooks"


@dataclass
class HookResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> dict:
        return json.loads(self.stdout) if self.stdout.strip() else {}

    @property
    def denied(self) -> bool:
        decision = self.output.get("hookSpecificOutput", {}).get("permissionDecision")
        return decision == "deny" or self.returncode == 2

    @property
    def reason(self) -> str:
        return self.output.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")

    @property
    def blocked_stop(self) -> bool:
        return self.output.get("decision") == "block"


@pytest.fixture
def sandbox_env(tmp_path: Path) -> dict:
    """A fake project and home directory, so tests never touch the real ones."""
    project = tmp_path / "project"
    home = tmp_path / "home"
    project.mkdir()
    home.mkdir()
    return {
        "project": project,
        "home": home,
        "env": {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(home),
            "CLAUDE_PROJECT_DIR": str(project),
        },
    }


@pytest.fixture
def run_hook(sandbox_env: dict):
    def _run(hook: str, payload: dict | str, env: dict | None = None) -> HookResult:
        data = payload if isinstance(payload, str) else json.dumps(payload)
        payload_env = dict(sandbox_env["env"])
        if env:
            payload_env.update(env)
        proc = subprocess.run(
            [sys.executable, str(HOOKS_DIR / hook)],
            input=data,
            capture_output=True,
            text=True,
            env=payload_env,
            timeout=30,
            check=False,
        )
        return HookResult(proc.returncode, proc.stdout, proc.stderr)

    return _run
