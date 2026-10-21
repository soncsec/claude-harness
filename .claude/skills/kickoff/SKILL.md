---
name: kickoff
description: Start a project the claude-harness way. Define it, design it, plan the first milestone as GitHub issues, then build a walking skeleton through CI, asking the operator only for the decisions that shape it. Use /kickoff for a new project, /kickoff --adopt for one that already has code.
argument-hint: "[--adopt]"
disable-model-invocation: true
---

# Kickoff

Take the project from an idea (or, with `--adopt`, from existing code) to a
planned first milestone and a walking skeleton: a minimal app that already
passes the whole pipeline, so feature work travels a road known to work.

Arguments: `$ARGUMENTS`. With `--adopt`, follow the changes in
[Adopting an existing project](#adopting-an-existing-project) as you go.

## Rules for the whole kickoff

- **Ask with AskUserQuestion, always.** Up to 4 questions per call, 2-4 options
  each, your recommendation first with "(Recommended)". The operator can type
  their own answer with "Other", so for open questions offer your best concrete
  guesses as options. Never ask in plain text.
- **Don't ask what the repo can tell you.** Read README.md, CLAUDE.md and the
  code first.
- **Keep going.** Ask only for the decisions that shape the project (phases 1
  and 2), the issue list (phase 3), and anything wrong with the setup. Don't
  stop for approval between phases: end each one with a short summary (under 5
  lines) and go straight on. The operator reviews the docs in the kickoff pull
  request.
- **Plain, short docs.** Fill every `<...>` in the templates from the
  operator's answers, and delete sections that don't apply. If something is
  unknown, ask; don't leave a placeholder or a TODO.
- **Never stop silently.** When the next step is a merge, watch the pull
  request as `.claude/skills/watch-pr/SKILL.md` describes, so the operator is
  notified and the kickoff carries on by itself once it is merged. When you need
  the operator to do anything else, ask with AskUserQuestion, with the exact
  steps in the question; never leave steps in plain text.
- **Normal guardrails apply.** Work on a branch, stage files by name, never
  touch `.claude/`, `.github/` or `quality-gates.toml`, and never merge.
- **State lives in the repo and GitHub, not in this chat.** If kickoff was
  interrupted, check what exists (README.md filled in, `docs/ARCHITECTURE.md`,
  `docs/threat-model.md`, a milestone with issues, the skeleton) and offer to
  continue from the first unfinished phase.

Templates are in `.claude/skills/kickoff/templates/`.

## Phase 0: Check the setup

Check quietly; ask only if something is wrong.

- `.claude/harness-version` exists, and `git remote get-url origin` is a GitHub repo.
- `gh repo view --json nameWithOwner,visibility` works (the agent's GitHub identity).
- `gh label list --json name --jq '.[].name'` includes `ready`, `needs-human` and `security`.
- Whether a `make check` target exists yet (a new project gets one in phase 4).

If anything is missing, explain it in one line each, with the operator's fix
(usually: run the harness's `scripts/github-setup.sh OWNER/REPO`, or
`scripts/update-guardrails.sh`), and ask: "Fix it first (Recommended)" /
"Continue anyway".

Then create the branch: `git switch --no-track -c kickoff`.

## Phase 1: Define

Find out, in at most three rounds of questions:

- **Goal:** the problem it solves, and what "working" looks like.
- **Users:** who uses it, and how (browser, API client, command line, other systems).
- **Non-goals:** what it will deliberately not do, at least for now.
- **Constraints:** deadline, hosting, budget, required tools or services.
- **Data sensitivity:** none, personal data, sensitive (health, money,
  credentials) or regulated. This sets how deep the threat model goes.

Write:

- `README.md` from `templates/README.md`. If a README exists, keep its content
  and add the missing sections.
- The top of `CLAUDE.md`: replace the description under the title with two to
  four sentences on what the project is, for whom, and a line `Data handled:
  <sensitivity>`. Leave the rest of `CLAUDE.md` alone.

Commit: `git add README.md CLAUDE.md` and `git commit`.

## Phase 2: Design

Decide, recommending the simplest choice that fits phase 1:

- **Shape:** web API, web app with pages, command-line tool, background worker or library.
- **Stack:** Python with uv is the harness default. For a web API or app,
  FastAPI; for a command-line tool, the standard library's argparse unless
  there is a reason for more.
- **Storage:** none, SQLite (a good start for one server), or PostgreSQL (many
  writers, or a managed database).
- **Sign-in:** none, username and password with sessions, API tokens, or an
  external provider (OpenID Connect).
- **External services and secrets:** each one is an entry point and a secret to manage.
- **Where it runs:** nowhere yet, or the operator's staging and production
  servers. Servers mean the harness's deploy path: a container image built on
  each merge, staging first, production after the operator approves, rootless
  Podman on the servers. It suits a long-running app such as a web API.
  Deploys need a public repo or a paid GitHub plan: if
  `gh repo view --json visibility` says PRIVATE, tell the operator before they
  choose servers (they may make the repo public, or upgrade).

Write:

- `docs/ARCHITECTURE.md` from `templates/ARCHITECTURE.md`.
- `docs/adr/0001-record-architecture-decisions.md`: we keep ADRs, one per
  significant decision, never edited once accepted (a new ADR replaces an old one).
- One ADR per decision above that was a real choice, from `templates/adr.md`,
  numbered `0002-...` onward, each with the alternatives considered.
- `docs/threat-model.md` from `templates/threat-model.md`. Go through each entry
  point and trust boundary with STRIDE. Every threat is Mitigated (by design or
  a built-in control), Planned (it becomes an issue in phase 3) or Accepted
  (only if the operator says so; record why).
- `SECURITY.md` from `templates/SECURITY.md`, if there is none.

Commit these files by name.

## Phase 3: Plan

1. Write `ROADMAP.md` from `templates/ROADMAP.md`: the first milestone in one
   sentence, then the next two or three, and the non-goals.
2. Draft the first milestone's issues, each small enough for one pull request:
   - Always first: **Walking skeleton**: the minimal app (a health endpoint, a
     `--version` command, or one function), its tests, the `make` targets and
     a green CI run.
   - Then the features that make the milestone true, 3 to 8 issues.
   - One issue per Planned threat, labeled `security`.
3. Show the list (title, one line each, labels) and ask: "Create them
   (Recommended)" / "Change the list". Approving the list is all the operator
   does: agents take issues this project's agent opened without a `ready` label.
4. Once approved:
   - Create the milestone: `gh api repos/{owner}/{repo}/milestones -f title="M1: <name>" -f description="<one sentence>"`.
   - Create each issue with `gh issue create --title ... --label ... --milestone "M1: <name>" --body-file <file>`.
     Write each body to a scratch file first, using the headings of the
     matching form in `.github/ISSUE_TEMPLATE/` (`### Goal`,
     `### Acceptance criteria`, `### Security impact`, `### Out of scope`).
     Acceptance criteria are testable statements; they become the first tests.
     End the body with `Approved by the operator during kickoff on <date>.`
   - Create exactly the approved issues, nothing more. Never add the `ready`
     label: it is for the operator to let in issues other people opened.
5. Commit `ROADMAP.md`, push the branch and open a pull request: "Kickoff:
   definition, design and first milestone", listing the docs and linking the
   milestone. Then watch it (`.claude/skills/watch-pr/SKILL.md`): the operator
   is notified when it is ready to merge, and its merge starts phase 4.

## Phase 4: Walking skeleton

Start when the watcher reports the kickoff pull request merged; there is nothing
to ask. (If the kickoff was interrupted, check with `gh pr view` that it is
merged.) Switch to `main` and pull as the watch-pr skill describes, then work
the Walking skeleton issue the normal way: plan it yourself (no approval
needed), tests first, `make check`, pull request with `Closes #<n>`.

For a Python project:

- `uv init --package --vcs none --no-readme --name <name> .`, then add the
  sections in `templates/python/pyproject-additions.toml` to `pyproject.toml`
  (including the 7-day supply-chain cooldown).
- Copy `templates/python/Makefile` and fill in the `run` target.
- Add `.venv/`, `__pycache__/` and `dist/` to `.gitignore`, outside the harness block.
- Dependencies: `uv add --dev pytest ruff`, plus what the ADRs chose, nothing
  more. Add them only with `uv add`, which asks the operator, never by editing
  `pyproject.toml`; say which packages and why before asking. Commit `uv.lock`.
- The smallest thing that proves the path: a web API gets an app factory
  and `GET /health` returning `{"status": "ok"}`, with a test through the HTTP
  client; a command-line tool gets `--version`; a library gets one function.

When the pull request is open, create a `needs-human` issue (with that form's
headings): "Turn on the coverage gate". Its exact step: run the harness's
`scripts/update-guardrails.sh` in this project after merging the skeleton; it
adds the new project to `quality-gates.toml`, which agents cannot edit.

If the design says it runs on the operator's servers, the skeleton also gets:

- `Containerfile` from `templates/python/Containerfile`, with its `CMD` filled
  in. The app reads `PORT` and keeps any files under `DATA_DIR` (`/data`); the
  image's filesystem is read-only.
- `requirements.txt` added to `.gitignore` (`make container-context` writes it).
- A second `needs-human` issue, "Turn on deploys", with the exact steps: run the
  harness's `scripts/setup-deploy.sh --staging deploy@<host> --production
  deploy@<host> .` in this project, run the server command it prints on each
  server, and merge the pull request that adds `.github/workflows/deploy.yml`.
  Agents cannot add workflows or deploy keys themselves.

If the project has a deploy workflow (`.github/workflows/deploy.yml`), the
skeleton is done only once it is live in staging and production with a clean
security scan. Until then, done means CI is green and the operator has merged.

Watch the skeleton pull request (`.claude/skills/watch-pr/SKILL.md`). Once it
is merged, give a short summary (what exists now, the milestone link), then
start the next issue without asking, as the watch-pr skill's step 3 describes.

## Adopting an existing project

With `--adopt`, the code is the first source of truth:

- **Phase 0:** also list which of README.md, `docs/ARCHITECTURE.md`,
  `docs/adr/`, `docs/threat-model.md`, ROADMAP.md, SECURITY.md, tests and a
  `make check` target exist.
- **Phases 1 and 2:** read the code, dependencies, tests and docs first, and
  draft from what is there. Ask only what the code cannot tell you: goals,
  users, non-goals, data sensitivity, and whether the existing choices are
  intended. Record existing choices as ADRs with status "Accepted (existing)".
- **Never overwrite.** Create missing files. For a file that exists, ask before
  changing it, and only add sections; never rewrite what is there.
- **Phase 3:** issues for the gaps too: a missing `make check`, untested core
  paths, Planned threats.
- **Phase 4:** skip it if `make check` exists and passes. Otherwise the first
  issue adds `make` targets around the tools the project already uses, with no
  new app code.
