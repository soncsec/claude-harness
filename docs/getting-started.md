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

**Private repos on GitHub Free:** GitHub offers rulesets (branch protection)
only on public repos, or on private ones with GitHub Pro. On a free private
repo, `new-project.sh` warns you up front, sets up everything else, and ends
with `Protection: local guardrails only`: agents are still stopped by the deny
rules, hooks and sandbox, but GitHub itself will not block pushes to `main` or
enforce CODEOWNERS. Use `--public`, or GitHub Pro, for both layers.

| Step | What happens |
| --- | --- |
| Guardrails | Settings, sandbox, hooks, `CLAUDE.md`, `.gitignore`. It asks for a project name and a one-line description. |
| CODEOWNERS | Changes to guardrail files need your review; everything else just needs you to click Merge. |
| First commit | A new, empty repo: pushed straight to `main`, before any rules exist. A repo with history: a pull request for you to merge. |
| CI | Checks and security scans on every pull request ([ci.md](ci.md)). |
| GitHub rules | Only pull requests reach `main`, and only once the CI checks pass; no force pushes; tags locked. |
| Issues | Feature, Bug, Chore and Needs-human forms, with blank issues turned off, and the labels agents use: `ready`, `needs-human`, `security`. |
| Agent identity | If you set up the app: it opens the app's settings, waits while you tick the new repo, then wires the project's own token. |

It prints how long it took and what to do next. Then `cd ~/dev/<name> && claude`
and type `/kickoff`.

### Kickoff: plan the project with Claude

`/kickoff` walks a new project from an idea to its first working code. Claude
asks you the questions that shape the project through the question prompt,
then keeps going on its own; you review the result in its pull requests:

| Phase | You decide | Claude writes |
| --- | --- | --- |
| 0. Check | Only if something is missing | Nothing; it checks the setup |
| 1. Define | Goal, users, non-goals, constraints, how sensitive the data is | `README.md`, the top of `CLAUDE.md` |
| 2. Design | App shape, storage, sign-in, services | `docs/ARCHITECTURE.md`, ADRs in `docs/adr/`, `docs/threat-model.md`, `SECURITY.md` |
| 3. Plan | Which issues make up the first milestone | `ROADMAP.md`, a milestone and the issues you approved, one pull request |
| 4. Skeleton | Its dependencies (each `uv add` asks you) | A minimal app with tests and `make check`, green in CI |

You never have to check on it. When a kickoff pull request is ready, Claude
notifies you (a desktop notification, and a phone push if Remote Control is
on) and watches it; once you merge, it carries on by itself. The same goes for
every pull request agents open later (the `watch-pr` skill): after each merge,
the agent starts the next issue without asking. It stops only for a
critical design decision (architecture, data, interfaces, security, costs or
scope) or something only you can do, and then it asks through the question
prompt, with the exact steps in the question.

Merging is your approval: you never label issues for the agent. It takes any
open issue opened by you, a collaborator or the agent itself, oldest first in
the earliest milestone (`.claude/tools/next_issue.py` picks it). On a public
repo anyone can open an issue, so an issue from anyone else waits until you
label it `ready`; the agent tells you which ones are waiting. It never takes
`needs-human` issues: those are steps for you.

After the skeleton is merged, run `scripts/update-guardrails.sh` once in the
project: it adds the new code to the coverage gate. Kickoff opens a
`needs-human` issue to remind you.

For a project that already has code, use `/kickoff --adopt`: Claude reads the
code first, asks only what it cannot tell, and adds missing docs and plans
without rewriting what is there.

## 3. Tailor it

See [customizing.md](customizing.md): which network domains your project needs,
what to allow without prompting, and how to add your own protected files.

### Deploys (optional)

If the project runs on your servers, turn on deploys once the skeleton is
merged: [deploy.md](deploy.md). Kickoff opens a `needs-human` issue with the
exact steps.

## 4. Prove it works (once per harness version)

Start a new Claude Code session in the project and walk through
[red-team-checklist.md](red-team-checklist.md). Every "must be blocked" item
should be blocked. If one is not, stop and fix it before real work starts.

## Doing it step by step

`new-project.sh` runs these; you can also run them yourself:

```sh
cd ~/dev/<project>
~/dev/claude-harness/scripts/install-harness.sh .                # guardrails
git add .claude .github CLAUDE.md .gitignore quality-gates.toml
git commit -m "Install claude-harness"
git push                                                          # before the rules exist
~/dev/claude-harness/scripts/github-setup.sh <owner>/<repo>       # rules + CODEOWNERS
# add the repo to the agent app: github.com/settings/installations, Configure
~/dev/claude-harness/scripts/agent-token.sh configure .           # agent identity
```

| Installed file | What it does |
| --- | --- |
| `.claude/settings.json` | Permissions, sandbox and hook wiring |
| `.claude/hooks/*.py` | Guardrail hooks (dangerous commands, secrets, questions, reminders) |
| `.claude/rules/harness.md` | The rules every agent follows: workflow, hard rules, when to ask you |
| `.claude/skills/` | The `/kickoff` and `watch-pr` skills |
| `.claude/tools/watch_pr.py` | Lets agents wait on a pull request's checks and merge in the background |
| `.claude/tools/next_issue.py` | Picks the next issue an agent may work on |
| `.claude/harness-version` | Which harness version is installed |
| `.github/workflows/`, `.github/harness/` | CI checks and the checkers they run ([ci.md](ci.md)) |
| `.github/ISSUE_TEMPLATE/` | Issue forms (only the ones you are missing) |
| `quality-gates.toml` | Coverage minimums CI enforces (only created if you have none) |
| `CLAUDE.md` | Notes about your project for agents (only created if you have none) |
| `.gitignore` | A marked block that keeps secrets and local files out of git |

It never overwrites your `CLAUDE.md`, and it shows a diff and asks before
replacing any other file that differs from the template. The harness's rules
live in `.claude/rules/harness.md`, which every update keeps current, so
`CLAUDE.md` holds only what is particular to your project. Older installs put
the rules in `CLAUDE.md`. The next update takes those sections out, but only
ones nobody edited. It lists any edited ones for you to merge by hand.

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
  files read-only (`.claude/`, `.github/workflows/`, `.github/harness/`,
  `.github/CODEOWNERS`, `.pre-commit-config.yaml`, `quality-gates.toml`,
  `.security/`, `.git/config`), so an agent cannot switch to
  a branch or pull a commit that changes them; git stops halfway with "Read-only
  file system" or "Device or resource busy". That is deliberate: otherwise
  checking out a branch with weaker settings would get around them. Do those
  switches and pulls yourself, outside the sandbox.
- **No GitHub credentials by default.** Your `gh` login is unreadable inside the sandbox, so
  agents cannot push or open pull requests with your account. Give agents their
  own GitHub identity, or push yourself.

## Updating

When the harness gets new guardrails (settings, hooks, CI workflows), one
command brings a project up to date:

```sh
git -C ~/dev/claude-harness pull
~/dev/claude-harness/scripts/update-guardrails.sh ~/dev/<project>
```

It shows what would change and asks once. Then it installs the changes on a
branch, opens a pull request, waits for CI to pass, merges it with your admin
rights, and re-applies the GitHub rules. A failing check stops it before the
merge. `--no-merge` stops after opening the pull request; `--dry-run` only
shows what would change.

Only you can do this: agents cannot install or change their own guardrails.
Ordinary work (code, docs, tests) never needs it.

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
