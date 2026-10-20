# {{PROJECT_NAME}}

{{DESCRIPTION}}

<!-- Installed by claude-harness. This file is loaded into every Claude Code
     session, so keep it short and link out to longer docs. -->

## Workflow

1. Work only on GitHub issues labeled `ready`: one issue, one branch, one small PR.
   Never add the `ready` label yourself, except as `/kickoff` describes: to the
   issues you just created from a list the operator approved. If the project has
   no plan yet (no `docs/ARCHITECTURE.md`, no `ready` issues), suggest the
   operator run `/kickoff` (or `/kickoff --adopt` for existing code).
2. Propose a plan first; the operator approves it before you write code.
3. Turn the issue's acceptance criteria into failing tests, then make them pass.
4. Run `make lint test scan` and fix what they report before saying you are done.
5. Open a pull request and tell the operator it is ready. Never push to `main`,
   never merge, never approve: the operator merges. PRs that touch guardrail
   files also wait for the operator's review.

## Commands

`make setup` · `make lint` · `make test` · `make scan` · `make build` · `make help`

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
  it partway. Ask the operator to do it.
- New dependencies need the operator's approval: add them with `uv add`, which
  asks, never by editing `pyproject.toml`. Check the package is real,
  maintained and spelled correctly before proposing it.

## Asking the operator

- Ask every question with the AskUserQuestion tool: 2-4 options, recommended first.
  Plain-text questions are easy to miss.
- Subagents cannot ask the operator: return the question to the main agent.
- Anything the operator must do later becomes a GitHub issue labeled `needs-human`
  with exact steps.
- `gh issue create` skips the issue forms, so write the body with the matching
  form's headings from `.github/ISSUE_TEMPLATE/` (`### Goal`,
  `### Acceptance criteria`, ...), and add the form's label.

## When something fails

Fix the code first. Rerun a flaky test once. After 3 failed attempts at the same
problem, stop and ask the operator with options.

## More

- Architecture: `docs/ARCHITECTURE.md`
- Decisions and their reasons: `docs/adr/`
