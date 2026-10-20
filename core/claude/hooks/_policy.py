"""Shared policy for the harness hooks: what counts as a secret or a guardrail file.

Hooks are a fast first line of defense that tell Claude *why* something was
blocked. They are heuristics. The OS-level sandbox and CI are the real
boundaries; nothing here should be the only thing standing between an agent
and a mistake that matters.
"""

from __future__ import annotations

import fnmatch
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

# Guardrail files: only the operator changes these; agents propose changes instead.
# Matched against the path relative to the project root, at any depth.
# A trailing slash means "this directory and everything in it".
PROTECTED_PATHS = (
    ".claude/",
    ".github/workflows/",
    ".github/harness/",
    ".github/CODEOWNERS",
    ".pre-commit-config.yaml",
    ".security/",
    "quality-gates.toml",
)

# Secret files, matched against the file name wherever it lives.
# Document config in `env.example` (no leading dot) so it stays readable.
SECRET_NAME_PATTERNS = (
    ".env",
    ".env.*",
    ".envrc",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "id_dsa*",
    ".netrc",
    ".pypirc",
    ".git-credentials",
    ".credentials.json",
)

# Secret locations in the user's home directory.
SECRET_HOME_DIRS = (".ssh", ".aws", ".gnupg", ".kube", ".config/gh", ".config/claude-harness")
SECRET_HOME_FILES = (".docker/config.json", ".claude/.credentials.json")

PROPOSAL_HINT = (
    "Guardrail files are operator-only. Write the change you want to "
    "docs/proposals/<topic>.md and ask the operator to apply it."
)

OFF_LIMITS_HINT = (
    "Secret files are off-limits to agents. If you need a value, ask the operator; "
    "document settings in env.example instead."
)


def read_hook_input(*, fail_closed: bool) -> dict:
    """Read the hook's JSON input from stdin.

    fail_closed=True blocks the tool call when the input is unreadable (exit 2),
    which is right for PreToolUse. Stop and SessionStart hooks fail open instead,
    so a hook bug can never trap a session.
    """
    try:
        data = json.load(sys.stdin)
        if not isinstance(data, dict):
            raise ValueError("input is not a JSON object")
        return data
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        if fail_closed:
            print(f"Harness hook could not read its input ({exc}); blocking.", file=sys.stderr)
            sys.exit(2)
        sys.exit(0)


def deny(reason: str) -> NoReturn:
    """Block a PreToolUse call and give Claude the reason."""
    decision = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    print(json.dumps(decision))
    sys.exit(0)


def project_dir(data: dict) -> Path:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd()
    return Path(os.path.realpath(root))


def resolve(path: str, base: Path) -> Path:
    """Absolute, symlink-free form of a path, so ../ and links cannot dodge a check."""
    candidate = Path(os.path.expanduser(path))
    if not candidate.is_absolute():
        candidate = base / candidate
    return Path(os.path.realpath(candidate))


def is_secret_path(path: Path) -> bool:
    if any(fnmatch.fnmatchcase(path.name, pattern) for pattern in SECRET_NAME_PATTERNS):
        return True
    try:
        rel = path.relative_to(Path(os.path.realpath(Path.home()))).as_posix()
    except ValueError:
        return False
    if any(rel == d or rel.startswith(d + "/") for d in SECRET_HOME_DIRS):
        return True
    return rel in SECRET_HOME_FILES


def protected_match(path: Path, project: Path) -> str | None:
    """Return the protected pattern a path falls under, or None."""
    try:
        parts = path.relative_to(project).parts
    except ValueError:
        return None
    # Check every tail of the path so nested copies (e.g. in a worktree) count too.
    for start in range(len(parts)):
        tail = "/".join(parts[start:])
        for pattern in PROTECTED_PATHS:
            if pattern.endswith("/"):
                if tail == pattern[:-1] or tail.startswith(pattern):
                    return pattern
            elif tail == pattern:
                return pattern
    return None
