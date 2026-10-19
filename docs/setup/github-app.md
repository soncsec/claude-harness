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
| Open and update pull requests, comment | Merge anything you have not approved (ruleset) |
| | Count as an approval (Code Owner review is yours) |
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

Agent PRs merge normally once you approve them. Your **own** PRs need your admin
bypass, because GitHub never lets you approve your own PR: on the PR page, tick
**Merge without waiting for requirements to be met (bypass rules)**.

**Stricter option:** to stop the agent merging even a PR you approved (say, one
you approved but wanted to land later), add `{ "type": "update" }` to the rules
in `core/github/ruleset-main.json` and re-run this step. Every merge, including
agent PRs, then needs your bypass.

## 5. Check it

```sh
scripts/agent-token.sh check
```

This mints a token, confirms which identity it belongs to, and revokes it again.

## 6. Keep the token fresh, and wire the project

```sh
scripts/agent-token.sh install-timer             # renews every 20 minutes, in the background
scripts/agent-token.sh configure ~/dev/<project> # Claude sessions there act as the app
scripts/agent-token.sh status                    # when does the current token expire?
```

`install-timer` sets up a systemd user timer. Every 20 minutes, and right after
login or waking from sleep, it mints a fresh 1-hour token into
`~/.cache/claude-harness/gh/` and revokes the previous one. `gh`, and `git`
through `gh`, read that file on every command, so running sessions pick up each
renewal without restarting.

`configure` writes the project's `.claude/settings.local.json`: personal,
git-ignored, and unwritable by agents. It points `gh` and `git` at the token
file and labels commits as `<app>[bot]`. Run it once per project. Your own `gh`
in your own terminal is unaffected.

## Daily use

Just run `claude` in the project. Nothing to restart, nothing to remember.

- Moved the harness repo? Run `install-timer` again: the timer stores its path.
- No systemd on this machine? Start sessions with `scripts/agent-session.sh`
  instead: it renews the token in the background for as long as Claude runs.

## Prove the boundary

Once, after setup, in a Claude session in the configured project:

- [ ] Ask Claude to push a test branch and open a PR: it works, and the PR
      shows `<app>[bot]` as the author.
- [ ] **Before you approve it**, ask Claude to merge that PR with `gh pr merge`,
      then with `--admin`, then with `--auto`: GitHub refuses all three. (Don't
      merge it yourself first, or the test has nothing to test.)
- [ ] Ask Claude to read `~/.config/claude-harness/agent-app.pem`: blocked.
- [ ] Approve the PR yourself: allowed, because you are not the author. It
      can now be merged.

## Known limit (stage 1)

The agent can read its current token from the token file. That token is
limited to one repo, the permissions above and at most 1 hour, it is revoked
when the next one is minted, and the rules stop it merging or approving. Stage 2 will hide the token from the agent entirely (sandbox
credential masking, or a GitHub MCP server that mints tokens itself) once that
is tested.
