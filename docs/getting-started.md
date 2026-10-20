# Getting started

Set up the machine once, then each new project takes one command and a couple
of minutes. It works for brand-new repos and for projects that already exist.

## 1. Prepare the machine (once)

On Debian 13:

```sh
git clone https://github.com/soncsec/claude-harness ~/dev/claude-harness
cd ~/dev/claude-harness
scripts/setup-debian.sh --check          # what is installed?
scripts/setup-debian.sh --with-docker    # install what is missing
```

Run the setup script as your normal user, not with `sudo`; it asks for your
password only for the steps that need it. When it offers to add you to the
`docker` group, the safe answer is no: that group is equivalent to root, for
you and for any agent running as you.

Other systems need the same tools: `git`, `python3`, `make`, `uv`, `gh`, and
for the sandbox on Linux, `bubblewrap` and `socat`. Log in to GitHub with
`gh auth login`.

**Optional, also once:** give agents their own GitHub identity, so they can
push branches and open pull requests while merging stays with you. Follow
[setup/github-app.md](setup/github-app.md) (about 10 minutes), then
`scripts/agent-token.sh install-timer`.

## 2. Set up a project (one command)

```sh
# A brand-new repo (private unless you add --public):
~/dev/claude-harness/scripts/new-project.sh --create <you>/<name> ~/dev/<name>

# An existing project that is already on GitHub:
~/dev/claude-harness/scripts/new-project.sh ~/dev/<project>
```

Add `--dry-run` first to see the plan without changing anything.

| Step | What happens |
| --- | --- |
| Guardrails | Settings, sandbox, hooks, `CLAUDE.md`, `.gitignore`. It asks for a project name and a one-line description. |
| CODEOWNERS | Every change needs your review. |
| First commit | A new, empty repo: pushed straight to `main`, before any rules exist. A repo with history: a pull request for you to merge. |
| GitHub rules | Only reviewed pull requests reach `main`; no force pushes; tags locked. |
| Agent identity | If you set up the app: it opens the app's settings, waits while you tick the new repo, then wires the project's own token. |

It prints how long it took and what to do next. Then `cd ~/dev/<name> && claude`,
and read `CLAUDE.md` to fill in anything marked `TODO`.

## 3. Tailor it

See [customizing.md](customizing.md): which network domains your project needs,
what to allow without prompting, and how to add your own protected files.

## 4. Prove it works (once per harness version)

Start a new Claude Code session in the project and walk through
[red-team-checklist.md](red-team-checklist.md). Every "must be blocked" item
should be blocked. If one is not, stop and fix it before real work starts.

## Doing it step by step

`new-project.sh` runs these; you can also run them yourself:

```sh
cd ~/dev/<project>
~/dev/claude-harness/scripts/install-harness.sh .                # guardrails
git add .claude CLAUDE.md .gitignore && git commit -m "Install claude-harness"
git push                                                          # before the rules exist
~/dev/claude-harness/scripts/github-setup.sh <owner>/<repo>       # rules + CODEOWNERS
# add the repo to the agent app: github.com/settings/installations, Configure
~/dev/claude-harness/scripts/agent-token.sh configure .           # agent identity
```

| Installed file | What it does |
| --- | --- |
| `.claude/settings.json` | Permissions, sandbox and hook wiring |
| `.claude/hooks/*.py` | Guardrail hooks (dangerous commands, secrets, questions, reminders) |
| `.claude/harness-version` | Which harness version is installed |
| `CLAUDE.md` | Starter instructions for agents (only created if you have none) |
| `.gitignore` | A marked block that keeps secrets and local files out of git |

It never overwrites your `CLAUDE.md`, and it shows a diff and asks before
replacing any other file that differs from the template.

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

> Install claude-harness into this project. Clone `https://github.com/soncsec/claude-harness` to
> `~/dev/claude-harness` if it is not there yet, then run
> `~/dev/claude-harness/scripts/install-harness.sh --yes .` from the project root.
> Show me the plan and the smoke-test result, fill in the TODO line in CLAUDE.md
> from what you know about this project, and stop. Do not commit; I will review
> the diff first.

Once the harness is installed, agents cannot re-run the installer or edit
`.claude/`: the Bash guard and the sandbox block it. Updates are an operator
step, so an agent can never replace its own guardrails.
