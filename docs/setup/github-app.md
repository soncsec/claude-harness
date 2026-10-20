# Give Claude its own GitHub identity

By default, agents in a harness project cannot use GitHub at all: your own `gh`
login is unreadable inside the sandbox, on purpose. Your token can merge,
approve deployments, change settings and delete the repo.

This guide gives agents their **own** identity: a GitHub App, installed only on
your repo. Agents then push branches and open pull requests as `<app>[bot]`, and
you review and merge them.

| The agent can | The agent cannot |
| --- | --- |
| Push branches | Push to `main` directly: every change needs a PR (ruleset) |
| Open and update pull requests, comment | Merge: you merge (deny rule + hook) |
| | Land a change to guardrail files without your review (ruleset + CODEOWNERS) |
| Create issues and labels (`needs-human`) | Change settings, rulesets, secrets or environments |
| Read CI results | Approve deployments |
| | Use a token for longer than 1 hour |

**Your part:** ordinary agent PRs need no approval; you look and click
**Merge**. PRs that touch guardrail files (`.claude/`, `.github/`,
`.security/`, `quality-gates.toml`) need your approval first.

Why a GitHub App and not a second account or your own token: an app's token
lasts 1 hour, works only on the repos you choose, and shows as `<app>[bot]` so
you can approve its PRs. A bot account would need a classic token that works on
every public repo on GitHub, and your own token would make you the author of
every agent PR, which GitHub never lets you approve.

No token permission can allow pushing but forbid merging, or allow commenting
but forbid approving. GitHub's rules (step 4) protect `main` and the guardrail
files; locally, the deny rules and the Bash hook stop agents merging.

## 1. Create the app

Open <https://github.com/settings/apps/new>:

- **GitHub App name:** something unique, for example `<you>-claude-agent`.
- **Homepage URL:** your repo's URL.
- **Callback URL:** empty. Leave **Request user authorization (OAuth) during
  installation** unticked.
- **Webhook:** untick **Active**.
- **Repository permissions:**

  | Permission | Access |
  | --- | --- |
  | Actions | Read-only |
  | Checks | Read-only |
  | Commit statuses | Read-only |
  | Contents | Read and write |
  | Issues | Read and write |
  | Metadata | Read-only |
  | Pull requests | Read and write |
  | Everything else | No access |

- **Account permissions:** all No access.
- **Where can this GitHub App be installed?** Only on this account.

Click **Create GitHub App** and note the **Client ID** on the app's page.

## 2. Install it on your repo

In the app's left menu: **Install App**, then **Install**, **Only select
repositories**, pick your repo, **Install**. One app serves all your projects:
for each new project you add its repo here (see "Adding another project").

## 3. Store the key and config

First make sure your project has the current harness guardrails installed: they
make `~/.config/claude-harness/` unreadable to agents. Then, on the app's page,
**Generate a private key** (your browser downloads a `.pem` file) and run:

```sh
mkdir -p ~/.config/claude-harness && chmod 700 ~/.config/claude-harness
mv ~/Downloads/<app-name>.*.private-key.pem ~/.config/claude-harness/agent-app.pem
chmod 600 ~/.config/claude-harness/agent-app.pem

cat > ~/.config/claude-harness/agent-app.conf <<'EOF'
CLIENT_ID=<client id from step 1>
APP_SLUG=<app name, lowercase, as in the app's URL>
EOF
chmod 600 ~/.config/claude-harness/agent-app.conf
```

The Client ID is in the **About** section of the app's settings page
(`https://github.com/settings/apps/<app-name>`) and starts with `Iv`. Never
paste the key into a chat, an issue or a commit.

## 4. Apply the rules

```sh
scripts/github-setup.sh --dry-run <owner>/<repo>   # preview
scripts/github-setup.sh <owner>/<repo>             # apply
```

This applies the `protect-main` and `protect-tags` rulesets, turns auto-merge
off, makes the Actions token read-only, and creates `.github/CODEOWNERS` naming
you. Commit `CODEOWNERS` through a pull request.

Ordinary PRs, the agent's and yours, merge with the normal **Merge** button: no
approval needed. A PR that touches a file listed in `CODEOWNERS` needs your
approval first; if it is your **own** PR, use your admin bypass instead (GitHub
never lets you approve your own PR): tick **Merge without waiting for
requirements to be met (bypass rules)**.

**Stricter options**, if a project needs them:

- Review every PR: set `required_approving_review_count` to 1 in
  `core/github/ruleset-main.json` (or add `* @you` to `CODEOWNERS`).
- Make GitHub itself stop the agent merging: add `{ "type": "update" }` to the
  rules. Every merge then needs your bypass.

Re-run this step after changing either.

## 5. Wire the project and keep its token fresh

```sh
scripts/agent-token.sh configure ~/dev/<project>  # once per project
scripts/agent-token.sh install-timer              # once per machine
scripts/agent-token.sh status                     # when does each token expire?
```

`configure` finds the project's repo from its `origin` remote (or pass
`--repo owner/name`), starts keeping a token for it, mints the first one, and
writes the project's `.claude/settings.local.json`: personal, git-ignored, and
unwritable by agents. That file points `gh` and `git` at the project's token
and labels commits as `<app>[bot]`. Your own `gh` in your own terminal is
unaffected.

Each project gets **its own token, limited to that one repo**, in
`~/.cache/claude-harness/<owner>/<repo>/`. An agent working on one project
cannot touch another.

`install-timer` sets up a systemd user timer. Every 20 minutes, and right after
login or waking from sleep, it renews every configured project's 1-hour token
and revokes the previous one. `gh`, and `git` through `gh`, read the token file
on every command, so running sessions pick up each renewal without restarting.

`scripts/agent-token.sh check` mints and revokes a token for every configured
project: a quick way to prove the key, the config and the installation work.

## Adding another project

1. Add its repo to the app: <https://github.com/settings/installations>, then
   **Configure** next to the app, then add the repo under
   **Only select repositories**, then **Save**.
2. `scripts/agent-token.sh configure ~/dev/<new-project>`

The timer picks it up automatically. To stop: `scripts/agent-token.sh forget owner/name`.

## Daily use

Just run `claude` in the project. Nothing to restart, nothing to remember.

- Moved the harness repo? Run `install-timer` again: the timer stores its path.
- Set up before per-project tokens (your `agent-app.conf` has `REPO=` and
  `INSTALLATION_ID=` lines)? Run `configure` again for that project, then
  delete those two lines and the old `~/.cache/claude-harness/gh/` folder.
  `status` reminds you while the old folder exists.
- No systemd on this machine? Start sessions with `scripts/agent-session.sh`
  instead: it renews the token in the background for as long as Claude runs.

## Prove the boundary

Once, after setup, in a Claude session in the configured project:

- [ ] Ask Claude to push a test branch and open a PR: it works, and the PR
      shows `<app>[bot]` as the author.
- [ ] Ask Claude to merge that PR with `gh pr merge`, and with
      `gh api -X PUT .../pulls/<n>/merge`: both blocked before they run.
      (Don't merge it yourself first, or the test has nothing to test.)
- [ ] Ask Claude to open a PR that changes `.github/CODEOWNERS` (it cannot
      edit it locally; have it propose the change in `docs/proposals/`
      instead) or check an existing guardrail PR: GitHub shows it needs your
      review.
- [ ] Ask Claude to read `~/.config/claude-harness/agent-app.pem`: blocked.
- [ ] Merge the test PR yourself with the normal **Merge** button.

## Known limit (stage 1)

The agent can read its current token from the token file. That token is
limited to one repo, the permissions above and at most 1 hour, it is revoked
when the next one is minted, and the rules stop it changing `main` or the
guardrail files without you. Stage 2 will hide the token from the agent entirely (sandbox
credential masking, or a GitHub MCP server that mints tokens itself) once that
is tested.
