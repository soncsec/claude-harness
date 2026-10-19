# claude-harness (developing the harness itself)

This repo builds the harness template that other projects install. These rules
are for agents working on the harness; the rules projects receive live in
`core/CLAUDE.template.md`.

## Where things live

- `core/` is the template source. Change hooks and settings **here**.
- `.claude/` is the installed, active copy for this repo. It is operator-only;
  agents never edit it. The operator updates it with `make install-harness TARGET=.`
- `scripts/` holds the setup and install scripts. `tests/` mirrors both.

## Rules

- Every hook change comes with tests in `tests/hooks/`: cases that must be blocked
  and everyday cases that must still be allowed. Run `make check` before finishing.
- Hooks use the Python standard library only. PreToolUse hooks fail closed;
  Stop and SessionStart hooks fail open, so a hook bug never traps a session.
- Shell scripts: bash, `set -euo pipefail`, shellcheck clean, Debian 13 first.
- When a rule changes in `core/claude/settings.json`, update `_policy.py` and the
  docs to match (`docs/customizing.md`, `docs/red-team-checklist.md`).
- Stage files by name, never `git add -A` or `git add .`: the sandbox shows empty
  placeholder files (`.bashrc`, `quality-gates.toml`, ...) that must not be committed.
- Run checks with `make`; it sets a per-project uv cache that works in the sandbox.
- This repo is public: never commit personal paths, hostnames, emails or tokens.
- Ask the operator every question with the AskUserQuestion tool.
