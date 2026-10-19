# Give Claude its own GitHub identity

By default, agents in a harness project cannot use GitHub at all: your own `gh`
login is unreadable inside the sandbox, on purpose. Your token can merge,
approve deployments, change settings and delete the repo.

This guide gives agents their **own** identity: a GitHub App, installed only on
your repo. Agents then push branches and open pull requests as `<app>[bot]`, and
you review and merge them.

| The agent can | The agent cannot |
| --- | --- |
| Push branches | Push to `main` or merge (ruleset) |
| Open and update pull requests, comment | Count as an approval (Code Owner review is yours) |
| Create issues and labels (`needs-human`) | Change settings, rulesets, secrets or environments |
| Read CI results | Approve deployments |
| | Use a token for longer than 1 hour |

Why a GitHub App and not a second account or your own token: an app's token
lasts 1 hour, works only on the repos you choose, and shows as `<app>[bot]` so
you can approve its PRs. A bot account would need a classic token that works on
every public repo on GitHub, and your own token would make you the author of
every agent PR, which GitHub never lets you approve.

No token permission can allow pushing but forbid merging, or allow commenting
but forbid approving. The **rules** applied in step 4 hold that line.

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

## 2. Install it on one repo

In the app's left menu: **Install App**, then **Install**, **Only select
repositories**, pick your repo, **Install**. The URL you land on ends in
`/installations/<number>`: that number is the **Installation ID**.

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
INSTALLATION_ID=<installation id from step 2>
REPO=<owner>/<repo>
APP_SLUG=<app name, lowercase, as in the app's URL>
EOF
chmod 600 ~/.config/claude-harness/agent-app.conf
```

Never paste the key into a chat, an issue or a commit.

## 4. Apply the rules

```sh
scripts/github-setup.sh --dry-run <owner>/<repo>   # preview
scripts/github-setup.sh <owner>/<repo>             # apply
```

This applies the `protect-main` and `protect-tags` rulesets, turns auto-merge
off, makes the Actions token read-only, and creates `.github/CODEOWNERS` naming
you. Commit `CODEOWNERS` through a pull request.

From now on your own PRs need your admin bypass to merge: on the PR page, tick
**Merge without waiting for requirements to be met (bypass rules)**.

## 5. Check it

```sh
scripts/agent-session.sh --check
```

This mints a token, shows which identity it belongs to, and revokes it again.

## Daily use

Start agent sessions with the script instead of `claude`:

```sh
scripts/agent-session.sh              # new session
scripts/agent-session.sh -- --resume  # arguments after -- go to claude
```

The token lasts 1 hour. When it runs out, pushes start failing: exit and run
the script again (`-- --resume` picks up the conversation). The token is
revoked when Claude exits.

## Prove the boundary

Once, after setup, in a session started with `agent-session.sh`:

- [ ] Ask Claude to push a test branch and open a PR: it works, and the PR
      shows `<app>[bot]` as the author.
- [ ] Ask Claude to merge that PR with `gh pr merge`: GitHub refuses.
- [ ] Ask Claude to read `~/.config/claude-harness/agent-app.pem`: blocked.
- [ ] Approve the PR yourself: allowed, because you are not the author.

## Known limit (stage 1)

The agent can see its own token in its environment. That token is limited to
one repo, the permissions above and 1 hour, and the rules stop it merging or
approving. Stage 2 will hide the token from the agent entirely (sandbox
credential masking, or a GitHub MCP server that mints tokens itself) once that
is tested.
