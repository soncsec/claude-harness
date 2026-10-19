# Proposals

Agents cannot change guardrail files: settings and hooks in `.claude/`, CI
workflows, `CODEOWNERS`, `quality-gates.toml` and `.security/`. When an agent
thinks one of them should change, it writes a proposal here instead, and the
operator decides.

## Writing one

Create `docs/proposals/<topic>.md` with:

1. **What should change:** the exact file and the exact new text, as a diff
   if possible.
2. **Why:** what went wrong or what is blocked without it.
3. **Risk:** what the change would allow that is not allowed today, and why
   that is acceptable.
4. **How to verify:** the command or red-team item that shows it works.

Open the proposal as a pull request like any other change.

## Applying one

The operator reviews the pull request. If accepted, the operator makes the
change in `core/` (for harness rules) or directly in the protected file, runs
`scripts/install-harness.sh .` if `core/` changed, and merges. The proposal
file can then be deleted or kept as a record.
