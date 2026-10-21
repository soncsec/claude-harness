# claude-harness rules

These are the rules for every agent in this project. They come from
claude-harness, which keeps this file up to date; agents cannot edit it.

Rules for this project alone go in `CLAUDE.md`. A rule written there for this
project can change the workflow below, but never the hard rules. Some older
installs copied the harness's rules into `CLAUDE.md` (headings such as
"Workflow" and "Asking the operator"). Those copies are out of date, for
example "the operator approves it before you write code": follow this file.

## Workflow

Keep going on your own. Stop only for a critical design decision (below) or a
pull request waiting to be merged.

1. Work only on the issue `python3 .claude/tools/next_issue.py` gives you: one
   issue, one branch, one small PR. It takes issues opened by the operator, a
   collaborator or this project's agent, and issues from anyone else only once
   the operator labels them `ready`. Never add the `ready` label yourself. Open
   issues only from a list the operator approved (as `/kickoff` does), or as
   `needs-human` steps for the operator; mention anything else you find in your
   PR description. If the project has no plan yet (no `docs/ARCHITECTURE.md`, no
   open issues), suggest the operator run `/kickoff` (or `/kickoff --adopt` for
   existing code).
2. Plan the change yourself and write the plan in the PR description. Ask the
   operator first only if the issue leaves a critical design decision open.
3. Turn the issue's acceptance criteria into failing tests, then make them pass.
4. Run `make lint test scan` and fix what they report before saying you are done.
5. Open a pull request, then watch it with the `watch-pr` skill: it waits for CI,
   tells the operator when the PR is ready, and carries on once it is merged.
   Never push to `main`, never merge, never approve: the operator merges. PRs
   that touch guardrail files also wait for the operator's review.
6. Once it is merged, start the next issue without asking (`next_issue.py` picks
   it). Merging is the operator's approval; they never have to label issues.
   With none left, ask the operator what is next.

### Critical design decisions

Ask before deciding anything that is hard to undo or changes what the product is
or how it is secured: the architecture, storage or data model, a public API or
interface, sign-in and permissions, how sensitive data is handled, a new external
service, anything that costs money, or work beyond the issue's scope. Record the
answer as an ADR in `docs/adr/`.

Everything else is yours to decide: names, code structure, test layout, small
trade-offs, which issue comes next. Pick the simplest option that fits
`docs/ARCHITECTURE.md`, note it in the PR description, and keep going.

## Hard rules

- Never weaken a test, a coverage threshold or a quality gate to get a pass.
- Never suppress a finding (`# nosec`, `# noqa`, `# nosemgrep`, `skip`, `xfail`);
  propose an exception to the operator instead.
- If a test change is genuinely needed (removed feature, wrong test), say so in
  the PR description: CI's `test-integrity` check fails it until the operator
  approves.
- Never read secret files (`.env`, keys, credentials). Document settings in `env.example`.
- Never edit guardrail files (`.claude/`, `.github/workflows/`, `quality-gates.toml`,
  `.security/`). Write the change you want to `docs/proposals/<topic>.md`.
- Never touch production. Deploys run in CI after the operator approves.
- Stage files by name (`git add path/one path/two`), never `git add -A` or `git add .`:
  the sandbox shows empty placeholder files over protected paths, and they must
  never be committed. Run checks through `make`, not by calling tools directly.
- Do not switch branches or pull when the change touches protected files
  (`.claude/`, `.github/`, `quality-gates.toml`, `.security/`): the sandbox blocks
  it partway. Ask the operator to do it with AskUserQuestion.
- New dependencies need the operator's approval: add them with `uv add`, which
  asks, never by editing `pyproject.toml`. Check the package is real,
  maintained and spelled correctly before proposing it.

## Asking the operator

- Ask every question with the AskUserQuestion tool: 2-4 options, recommended first.
  Plain-text questions are easy to miss. Ask only about critical design
  decisions and what you cannot do yourself.
- Never stop silently while the next step is the operator's. For a merge, watch
  the pull request (the `watch-pr` skill), which notifies them. For anything else
  you need them to do now (run a command, restart Claude, change a setting), ask
  with AskUserQuestion: put the exact steps in the question, with options such as
  "Done" and "Later". Never leave steps in plain text at the end of a reply.
- Subagents cannot ask the operator: return the question to the main agent.
- Anything the operator must do later becomes a GitHub issue labeled `needs-human`
  with exact steps.
- `gh issue create` skips the issue forms, so write the body with the matching
  form's headings from `.github/ISSUE_TEMPLATE/` (`### Goal`,
  `### Acceptance criteria`, ...), and add the form's label.

## When something fails

Fix the code first. Rerun a flaky test once. After 3 failed attempts at the same
problem, stop and ask the operator with options.
