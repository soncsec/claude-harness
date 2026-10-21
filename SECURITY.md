# Security policy

claude-harness is a security tool: people install it to keep AI coding agents
inside safe limits. A weakness here can weaken every project that uses it, so
reports are very welcome.

## Reporting a vulnerability

Report privately through GitHub: open the repository's **Security** tab and
choose **Report a vulnerability**
(<https://github.com/soncsec/claude-harness/security/advisories/new>).

Please do **not** open a public issue, pull request or discussion for a
vulnerability until a fix is released.

Helpful details:

- What an agent (or attacker) can do that it should not be able to do
- The exact command, file or prompt that shows it
- Harness version (`.claude/harness-version`), Claude Code version and OS
- Which layers it gets past: permission rules, hooks, sandbox, CI

## What to expect

This is a small project with a single maintainer.

- Acknowledgement within 7 days
- A fix or mitigation plan for critical issues within 30 days
- Credit in the advisory and release notes, unless you prefer to stay anonymous

## Scope

In scope:

- Everything in `core/`: hooks, settings, agent rules, skills and tools, CI
  workflows and checkers, rulesets and the CODEOWNERS template
- The scripts in `scripts/`, including the GitHub App token handling
  (`agent-token.sh`, `agent-session.sh`)
- Documentation that would lead users to an insecure setup

How severity is judged:

- **Higher:** a way past several layers at once, for example an agent reading a
  secret or changing its own guardrails despite both the hooks and the sandbox
- **Lower, still welcome:** a way past one hook that another layer still stops.
  Hooks are deliberately simple pattern checks and are not the only defense.

Out of scope (please report to the vendor instead):

- Claude Code itself, including its sandbox and permission system (Anthropic)
- GitHub, Debian packages, uv and other third-party tools

## How the harness is meant to be safe

Defense in depth: permission rules, hooks, an OS-level sandbox and CI checks
each cover gaps in the others. See
[docs/red-team-checklist.md](docs/red-team-checklist.md) for how each layer is
tested.
