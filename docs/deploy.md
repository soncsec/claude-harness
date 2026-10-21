# Deploys: from a merge to production

Deploys are optional. A project that turns them on gets this path:

1. Every merge to `main` builds a container image and pushes it to ghcr.io.
   The locked dependencies are audited again first, in case an advisory came out
   after the pull request was checked.
2. The image goes to **staging** at once. The server checks its health and rolls
   back to the previous release if the check fails.
3. **Production** waits for your approval in GitHub (Actions, then the run, then
   **Review deployments**). Then the same image, by digest, goes to production,
   with the same health check and rollback.

Agents never deploy. They cannot approve deployments, read the deploy keys,
reach your servers (the sandbox only lets them reach PyPI and GitHub), or
change the workflow.

## What runs where

| Where | What | Set up by |
| --- | --- | --- |
| GitHub | `.github/workflows/deploy.yml`; the `staging` and `production` environments (production has you as required reviewer, and both accept deploys from `main` only); per-environment `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS` and `DEPLOY_HOST` | `scripts/setup-deploy.sh`, on your machine |
| Each server | A `deploy` account running the app with rootless Podman under systemd; `deploy-receive.sh`, the only command CI's key may run | `scripts/setup-deploy-host.sh`, on the server |
| The project | `Containerfile`, and a `make container-context` target if the image needs files built first | `/kickoff` for new projects, or you |

## Turning deploys on

You need:

- **A public repo, or a paid plan.** Deploy secrets and the production approval
  live in GitHub environments, which GitHub ignores in private repos on GitHub
  Free. `setup-deploy.sh` checks this first and stops if deploys cannot work.
  Create the project as public (`--public` when you set it up), or make it
  public later with `gh repo edit <owner>/<repo> --visibility public
  --accept-visibility-change-consequences`.
- Two Debian 13 servers that GitHub's runners can reach over SSH, and that can
  reach ghcr.io.
- `main` as the project's default branch.

1. On your machine, in the harness:

   ```sh
   scripts/setup-deploy.sh --staging deploy@staging.example.com \
     --production deploy@app.example.com ~/dev/<project>
   ```

   It creates the environments and a new deploy key for each one, stores each
   server's SSH host keys (compare the fingerprints it prints with
   `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on the server), and copies
   the workflow into the project. Then it prints one command per server.

2. On each server, with the harness cloned there, run the command it printed:

   ```sh
   sudo scripts/setup-deploy-host.sh --app <name> --image ghcr.io/<owner>/<repo> \
     --port 8000 --health-path /health --ci-key "ssh-ed25519 AAAA..."
   ```

   For a private repo, add `--pull-user <login> --pull-token-file <file>`, where
   the file holds a classic token with only the `read:packages` scope. For a
   public repo you can instead make the package public after the first build.

3. Put the app's settings and secrets in `~deploy/<name>.env` on each server.
   They never go in git.

4. Commit `.github/workflows/deploy.yml` through a pull request and merge it.
   The merge runs the first deploy.

The app listens on `127.0.0.1:<port>` only. Put a reverse proxy (Caddy, nginx)
in front of it for outside traffic and TLS.

## What the app must do

- Listen on `0.0.0.0:$PORT` inside the container.
- Answer `GET /health` (or your `--health-path`) with a 2xx once it is ready.
- Write only to `$DATA_DIR` (`/data`). The rest of the filesystem is read-only.
  `/data` is `~deploy/<name>-data` on the server, so it survives new releases.
- Read settings from environment variables, which come from `~deploy/<name>.env`.

## How each step is protected

| Threat | What stops it |
| --- | --- |
| A pull request deploys, or reads the deploy keys | The workflow runs only on pushes to `main`, and the environments accept deploys from `main` only |
| Something reaches production without you | The `production` environment needs your approval, and agents cannot approve deployments |
| The CI key is stolen | It can only run `deploy-receive.sh`, which accepts only `<image>@sha256:<digest>` of the configured image (no shell, port forwarding or other images). Staging and production have different keys |
| Someone pretends to be your server | CI checks the server against the stored host keys (`StrictHostKeyChecking=yes`) |
| A bad release | The health check fails and the server switches back to the previous image |
| The app is compromised | It runs as an unprivileged account in rootless Podman, with a read-only filesystem, no Linux capabilities and no new privileges. It is not root on the host |
| A new advisory since the PR | The build audits the locked dependencies again and stops on a known vulnerability |

On private repos with GitHub Free, environments do not work at all.
`setup-deploy.sh` stops before changing anything, and it also stops before
creating deploy keys whenever production cannot require your approval.

## Day to day

- **Logs:** `sudo -u deploy journalctl --user -u <name>` on the server.
- **What is live:** `sudo -u deploy podman image inspect localhost/<name>:current`.
- **Roll back by hand:** re-run an older run's production job (it deploys that run's image), or on
  the server tag `localhost/<name>:previous` as `:current` and restart the unit.
- **Rotate the deploy keys:** run `setup-deploy.sh` again, then the server
  commands it prints. The old keys stop working.
- **The base image** (`FROM` in the `Containerfile`) is refreshed on every build
  (`--pull=always`). Pin it to a digest if you need builds to be reproducible.
