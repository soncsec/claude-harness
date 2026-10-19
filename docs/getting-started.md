# Getting started

This guide takes a project from nothing to working guardrails in about ten minutes.
It works for a brand-new project and for one that already exists.

## 1. Prepare the machine (once)

On Debian 13:

```sh
git clone <harness-repo-url> ~/dev/claude-harness
cd ~/dev/claude-harness
scripts/setup-debian.sh --check          # what is installed?
scripts/setup-debian.sh --with-docker    # install what is missing
```

Run the setup script as your normal user, not with `sudo`; it asks for your
password only for the steps that need it. When it offers to add you to the
`docker` group, the safe answer is no: that group is equivalent to root, for
you and for any agent running as you.

Other systems need the same tools: `git`, `python3`, `make`, `uv`, `gh`, and
for the sandbox on Linux, `bubblewrap` and `socat`.

## 2. Install the harness into your project

```sh
cd ~/dev/my-project          # or: mkdir my-project && cd my-project && git init
~/dev/claude-harness/scripts/install-harness.sh --dry-run .   # preview
~/dev/claude-harness/scripts/install-harness.sh .             # install
```

The installer shows its plan, asks before installing, and asks for a project
name and a one-line description for `CLAUDE.md`.

| Installed file | What it does |
| --- | --- |
| `.claude/settings.json` | Permissions, sandbox and hook wiring |
| `.claude/hooks/*.py` | Guardrail hooks (dangerous commands, secrets, questions, reminders) |
| `.claude/harness-version` | Which harness version is installed |
| `CLAUDE.md` | Starter instructions for agents (only created if you have none) |
| `.gitignore` | A marked block that keeps secrets and local files out of git |

It never overwrites your `CLAUDE.md`, and it shows a diff and asks before
replacing any other file that differs from the template.

## 3. Review and commit

```sh
git status
git diff
git add .claude CLAUDE.md .gitignore
git commit -m "Install claude-harness guardrails"
```

Read `CLAUDE.md` and fill in anything marked `TODO`.

## 4. Tailor it

See [customizing.md](customizing.md): which network domains your project needs,
what to allow without prompting, and how to add your own protected files.

## 5. Prove it works

Start a new Claude Code session in the project and walk through
[red-team-checklist.md](red-team-checklist.md). Every "must be blocked" item
should be blocked. If one is not, stop and fix it before real work starts.

## 6. Optional: give Claude its own GitHub identity

Agents cannot use your GitHub login. To let them push branches and open pull
requests under their own name while merging stays with you, follow
[setup/github-app.md](setup/github-app.md).

## What you will notice inside the sandbox

- **Empty placeholder files.** In a sandboxed session, `git status` shows empty files
  such as `.bashrc`, `.gitconfig`, `.mcp.json` or `quality-gates.toml`. The sandbox
  mounts them read-only over protected paths that do not exist yet, so nothing can
  create them. They exist only inside the sandbox, not on your disk. Never commit
  them: stage files by name. The Bash guard blocks `git add -A` and `git add .`.
  Git may also warn that it cannot read `.gitmodules`; that is the same
  placeholder and is harmless.
- **No shared caches.** Tools cannot write to caches in your home folder. The
  Makefile gives uv a per-project cache in `.cache/uv`; run checks with `make`.
- **Guardrail changes from git are an operator step.** The sandbox makes protected
  files read-only (`.claude/`, `.github/workflows/`, `.github/CODEOWNERS`,
  `quality-gates.toml`, `.security/`, `.git/config`), so an agent cannot switch to
  a branch or pull a commit that changes them; git stops halfway with "Read-only
  file system" or "Device or resource busy". That is deliberate: otherwise
  checking out a branch with weaker settings would get around them. Do those
  switches and pulls yourself, outside the sandbox.
- **No GitHub credentials by default.** Your `gh` login is unreadable inside the sandbox, so
  agents cannot push or open pull requests with your account. Give agents their
  own GitHub identity, or push yourself.

## Updating

Pull the latest harness, then re-run the installer. Files you have not changed
are updated; files you changed are shown as a diff and kept unless you agree to
replace them (or pass `--force`).

```sh
git -C ~/dev/claude-harness pull
~/dev/claude-harness/scripts/install-harness.sh .
```

## Letting Claude do the first install

In a project that does **not** have the harness yet, you can ask Claude to
install it. Paste this into a Claude Code session in that project:

> Install claude-harness into this project. Clone `<harness-repo-url>` to
> `~/dev/claude-harness` if it is not there yet, then run
> `~/dev/claude-harness/scripts/install-harness.sh --yes .` from the project root.
> Show me the plan and the smoke-test result, fill in the TODO line in CLAUDE.md
> from what you know about this project, and stop. Do not commit; I will review
> the diff first.

Once the harness is installed, agents cannot re-run the installer or edit
`.claude/`: the Bash guard and the sandbox block it. Updates are an operator
step, so an agent can never replace its own guardrails.
