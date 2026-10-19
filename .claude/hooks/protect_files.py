#!/usr/bin/env python3
"""PreToolUse hook for file tools: keep agents out of secrets and guardrail files.

- Read, Grep, Glob: blocked on secret files and secret folders.
- Edit, Write, NotebookEdit: blocked on secrets and on guardrail files
  (settings, hooks, CI workflows, quality gates), which only the operator changes.

Paths are resolved first, so ../ tricks and symlinks are checked by their real target.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _policy import (  # noqa: E402
    OFF_LIMITS_HINT,
    PROPOSAL_HINT,
    deny,
    is_secret_path,
    project_dir,
    protected_match,
    read_hook_input,
    resolve,
)

READ_ONLY_TOOLS = {"Read", "Grep", "Glob"}


def main() -> int:
    data = read_hook_input(fail_closed=True)
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    raw_path = (
        tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path")
    )
    if not isinstance(raw_path, str) or not raw_path:
        return 0

    project = project_dir(data)
    path = resolve(raw_path, Path(data.get("cwd") or project))

    if is_secret_path(path):
        deny(f"Blocked by harness guardrail (secret file: {path.name}). {OFF_LIMITS_HINT}")

    if tool not in READ_ONLY_TOOLS:
        pattern = protected_match(path, project)
        if pattern:
            deny(f"Blocked by harness guardrail (protected path: {pattern}). {PROPOSAL_HINT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
