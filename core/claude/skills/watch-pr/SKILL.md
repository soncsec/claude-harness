---
name: watch-pr
description: Watch a pull request after opening it or pushing to it. Waits for CI in the background, fixes failed checks, tells the operator when it is ready to merge, and carries on by itself once it is merged, on to the next ready issue. Use every time you open a pull request or push a fix to one.
argument-hint: "<pull request number>"
---

# Watch a pull request

Pull request: `$ARGUMENTS` (if empty, use the one you just opened).

The operator should never have to check on this session: they hear when the
pull request needs them, and the session carries on when it is merged. Run
each watch with Bash in the background (`run_in_background: true`). You are
woken with one line when it ends; meanwhile you can stop or do other work.

## 1. Wait for the checks

`python3 .claude/tools/watch_pr.py <number>`

| Line starts with | Do this |
| --- | --- |
| `failed:` | Read the failing logs (`gh pr checks <number>`, then the run's log), fix the code, push, and watch again. After 3 attempts at the same failure, stop and ask the operator with options. |
| `passed:` | Go to step 2. If the merge state is `DIRTY` (conflicts) or `BEHIND`, update the branch first and watch again. |
| `no-checks:` or `error:` | Tell the operator with AskUserQuestion, with options. |
| `timeout:` | Watch again. |
| `merged:` or `closed:` | Go to step 3. |

## 2. Tell the operator, then wait for the merge

1. Send a PushNotification: `PR #<number> ready to merge: <title>` (for a pull
   request that touches guardrail files: `ready for your review`).
2. Run `python3 .claude/tools/watch_pr.py <number> --until merged` in the background.
3. End your turn with one line: the pull request link, that it is ready to
   merge, and that you are watching it. Don't ask anything: merging is the answer.

On `timeout:`, run the same watch again without telling anyone.

## 3. After it ends

- `merged:` run `git fetch origin`. If `git diff --name-only HEAD origin/main`
  lists no protected files (`.claude/`, `.github/`, `quality-gates.toml`,
  `.security/`), run `git switch main && git pull --ff-only`. Otherwise ask the
  operator with AskUserQuestion to run that command in this project (put it in
  the question, with options "Done" and "Later"), and wait for their answer.
  Then carry on without asking: the next step of the task you were given or,
  with nothing left, the next issue (see below).
- **The next issue:** run `python3 .claude/tools/next_issue.py`. On `next:`,
  work that issue as `.claude/rules/harness.md` describes, planning it
  yourself: don't wait for a plan approval or a `ready` label,
  even if an older `CLAUDE.md` asks for one. The merge was the operator's approval. Ask the
  operator only about a critical design decision (one that is hard to undo, or
  changes the architecture, data, interfaces, security, costs or scope). On
  `none:`, send a PushNotification and ask with AskUserQuestion what comes
  next, naming any issues it lists as waiting for the `ready` label. On
  `error:`, tell the operator with AskUserQuestion, with options.
- `closed:` it was closed without merging. Ask the operator what happened, with
  options: reopen and change it, or drop the work.
