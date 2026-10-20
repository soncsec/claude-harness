# CI: the checks on every pull request

Every pull request runs these checks on GitHub. With the GitHub rules applied
(`scripts/github-setup.sh`), a pull request cannot be merged until the required
ones pass, whoever opened it.

| Check | Workflow | What it does | Fails when | Required |
| --- | --- | --- | --- | --- |
| `check` | `ci.yml` | `make check`: lint, tests, dependency scan | Anything in it fails | Yes |
| `secrets` | `security.yml` | gitleaks over every commit in the PR | A secret appears anywhere in the history | Yes |
| `sast` | `security.yml` | Semgrep code scan (`p/default`, `p/python`) | An error-severity finding | Yes |
| `dependencies` | `security.yml` | pip-audit on every `uv.lock` | A locked package has a known vulnerability | Yes |
| `workflows` | `security.yml` | zizmor on `.github/workflows` | A workflow has a security issue | Yes |
| `dependency-review` | `security.yml` | GitHub's review of newly added dependencies | A new dependency has a high or critical vulnerability | No (public repos only) |

`security.yml` also runs weekly on `main`: new vulnerabilities are found in
code that has not changed.

Run the same checks locally with `make check`. Secrets and Semgrep run in CI
only: they need network access the local sandbox does not allow.

## How the workflows are protected

- **Agents cannot change them.** `.github/workflows/` is a protected path, the
  agent's GitHub App has no workflow permission, and CODEOWNERS requires your
  review. The sources live in `core/github/workflows/`; `install-harness.sh`
  copies them into place.
- **No permissions by default.** Each job gets only `contents: read` (plus
  `pull-requests: read` for the secret scan). Checkouts do not keep the token.
- **Every action is pinned to a commit**, with its version in a comment. A tag
  can be moved to point at different code; a commit cannot.
- **Releases younger than 7 days are not adopted**, for actions and for the
  scanner versions alike. Malicious releases are usually caught and pulled
  within days.

`tests/test_workflows.py` checks all of this, so a careless edit fails `make check`.

## When a check fails

| Check | Look at | Usual fix |
| --- | --- | --- |
| `check` | The job log: the first failing lint or test | Fix the code, never the test or the threshold |
| `secrets` | gitleaks names the file and commit | Rotate the secret first (it is exposed once pushed), then remove it from the history |
| `sast` | Semgrep prints the rule and line | Fix the pattern; an accepted exception needs the operator |
| `dependencies` | pip-audit names the package and advisory | Upgrade it (`uv lock --upgrade-package <name>`), respecting the 7-day cooldown |
| `workflows` | zizmor names the workflow line | Fix it in `core/github/workflows/`, then re-install |

## Updating a pinned action or scanner

1. Pick a release at least 7 days old and read its release notes.
2. Resolve its tag to a commit: `gh api repos/<owner>/<action>/commits/<tag> --jq .sha`.
3. Update the commit and the version comment in `core/github/workflows/`, and
   the matching version in the Makefiles for pip-audit and zizmor.
4. Run `make check` and open a pull request. Once it is merged, the operator
   runs `scripts/update-guardrails.sh` to put the new workflows in place.

## Private repos on GitHub Free

The checks run, but GitHub cannot require them: rulesets are not available.
A failing check is then advice, not a gate. See
[getting-started.md](getting-started.md#2-set-up-a-project-one-command).
