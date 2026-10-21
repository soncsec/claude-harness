# Changelog

Notable changes to claude-harness. Projects record which version they run in
`.claude/harness-version`; `scripts/update-guardrails.sh` brings them up to date.

## Unreleased

### Deploys

- Optional deploy path (`docs/deploy.md`): every merge builds a container image,
  re-audits dependencies and deploys to staging; production waits for the
  operator's approval in a GitHub environment.
- Servers run the app with rootless Podman under systemd
  (`scripts/setup-deploy-host.sh`). CI's per-environment deploy key can only run
  `deploy-receive.sh`, which accepts only an exact digest of the configured
  image and rolls back if the health check fails.
- `scripts/setup-deploy.sh` creates the environments, deploy keys, host keys and
  workflow for a project. Updates keep `deploy.yml` current only in projects
  that have it.
- `/kickoff` adds a `Containerfile` and `make container-context` for projects
  that run on servers, plus a `needs-human` issue to turn deploys on.

- Servers on a private network: `setup-deploy.sh --runner LABEL` runs the
  staging and production jobs on a runner inside your network, installed by
  `scripts/setup-deploy-runner.sh` (pinned, checksum-checked, one repo, one
  label). On public repos, outside contributors' workflows then need approval.

### Fixes

- `github-setup.sh` turns on Dependabot alerts on public repos, which turns on
  the dependency graph that the `dependency-review` check needs. GitHub leaves
  it off when a private repo is made public, so that check failed.
- `setup-deploy.sh` no longer sets `wait_timer`, which GitHub Free refuses
  even at 0. It now checks first that the repo can use environments (public,
  or a paid plan) and stops with the options before changing anything.
- The Stop hook no longer treats "nothing is waiting on you" as a step left to
  the operator.

## 0.1.0 - 2026-10-21

The first release. Tested on Debian 13 with one trial project, built by agents
from an idea through three milestones.

### Guardrails

- Permission rules, an OS-level sandbox and hooks that keep agents away from
  secrets, block dangerous commands and stop them editing their own rules.
- Hooks fail safe: tool checks block when they break; Stop and SessionStart
  hooks let the session carry on.
- Agents propose guardrail changes in `docs/proposals/`; only the operator
  applies them.
- A red-team checklist to prove every layer works after an install or update.

### Setting up projects

- `scripts/setup-debian.sh` installs the tools; `scripts/new-project.sh` sets
  up a new or existing GitHub project in one command.
- `scripts/update-guardrails.sh` updates a project through a pull request, CI
  and a merge.
- Private repos on GitHub Free get every local layer, with a clear note that
  GitHub itself cannot enforce the rules there.

### Agents on GitHub

- Agents act as a GitHub App bot with short-lived, one-repo tokens, renewed in
  the background. They push branches and open pull requests; the operator merges.
- Rulesets protect `main` and tags; CODEOWNERS asks for the operator's review
  only on guardrail files.

### CI

- Every pull request runs lint, tests, secret scanning, Semgrep, pip-audit,
  zizmor and a coverage gate.
- A test-integrity check needs a maintainer's approval before tests are
  deleted, weakened, skipped or suppressed.
- Actions are pinned to commits, and releases younger than 7 days are not adopted.

### Workflow

- `/kickoff` takes a project from an idea to a design, a threat model, a first
  milestone and a walking skeleton.
- Agents keep going on their own: they pick the next issue, watch their pull
  requests, and carry on after each merge. Merging is the operator's approval.
- They stop only for critical design decisions, and ask through the question
  prompt, never in plain text. A Stop hook sends plain-text questions and
  leftover steps back.
- Issues from outside the team wait for the operator's `ready` label.
- Each session starts with what is waiting on the operator in GitHub.
