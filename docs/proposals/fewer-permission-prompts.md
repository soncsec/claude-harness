# Proposal: drop permission prompts for routine git and gh

**Status:** proposed by the agent on 2026-10-19, at the operator's request.
The agent's attempt to make this change was blocked by Claude Code's auto-mode
check as "self-modification", which is the harness working as intended: the
operator applies changes that loosen guardrails.

## What should change

In `core/claude/settings.json`, `permissions.ask` should become:

```json
"ask": [
  "Bash(uv add *)",
  "Bash(uv remove *)",
  "Bash(pip install *)",
  "Bash(uv pip install *)",
  "WebFetch"
],
```

Removed from `ask`: `Bash(git push *)`, `Bash(gh pr create *)`,
`Bash(gh api *)`, `Bash(gh workflow run *)`, `Bash(gh release *)`,
`Bash(docker *)`. (`Bash(gh pr merge *)` moves to `deny` in the same pull
request; that part is already done.)

## Why

The operator found the prompts unworkable: every push, pull request and `gh`
call needed a click. They were added before agents had their own GitHub
identity. Now GitHub enforces the important limits itself, and the operator
wants prompts only for new dependencies.

## Risk

Without prompts, the agent can push branches, open pull requests and call the
GitHub API on its own. What still bounds it:

- **The token:** one repo, no administration, secrets, environments,
  deployments or workflow permissions, at most 1 hour old.
- **GitHub rules:** no direct pushes to `main`, no force pushes, tags locked,
  Code Owner review on guardrail files.
- **Local layers:** deny rules and the Bash hook block merging, approving,
  deployment approvals, secrets and force pushes; the sandbox limits network
  access to PyPI and GitHub.

What it newly allows without a click: deleting or renaming its own branches,
closing issues, editing labels, and calling read and write API endpoints that
the token permits. `docker` stays unusable for agents (they are not in the
`docker` group). `WebFetch` keeps its prompt because it runs outside the
sandbox and could send data anywhere.

## How to verify

- `make check`: `tests/test_settings_policy.py` keeps passing.
- Red-team checklist: pushing a branch and opening a PR run without a prompt;
  `uv add` and a WebFetch still ask.

## How to apply

In your own terminal, on the `lighter-review` branch:

```sh
python3 - <<'EOF'
import json, pathlib
p = pathlib.Path("core/claude/settings.json")
s = json.loads(p.read_text())
s["permissions"]["ask"] = [
    "Bash(uv add *)", "Bash(uv remove *)", "Bash(pip install *)",
    "Bash(uv pip install *)", "WebFetch",
]
p.write_text(json.dumps(s, indent=2) + "\n")
EOF
git diff core/claude/settings.json      # review it
git add core/claude/settings.json
git commit -m "Drop permission prompts for routine git and gh"
```
