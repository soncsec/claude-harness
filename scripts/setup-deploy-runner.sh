#!/usr/bin/env bash
#
# setup-deploy-runner.sh - run a project's deploy jobs on a machine inside your
# network, for servers that GitHub's own runners cannot reach.
#
# Run it as root on a machine that can reach the staging and production servers
# over SSH. It installs GitHub's runner (a pinned, checksum-checked release) as a
# locked-down system user, registered to one repo with only the deploy label, so
# it only ever picks up the deploy workflow's staging and production jobs.
# The registration token comes on stdin, so it never shows in ps or your history:
#
#   gh api -X POST repos/OWNER/REPO/actions/runners/registration-token --jq .token \
#     | sudo scripts/setup-deploy-runner.sh --repo OWNER/REPO --label LABEL

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: TOKEN | sudo scripts/setup-deploy-runner.sh --repo OWNER/REPO --label LABEL [options]

  --repo OWNER/REPO  the project whose deploy jobs this runner takes
  --label LABEL      the runner's only label: the repo's DEPLOY_RUNNER variable
  --user USER        the system account it runs as (default gh-runner)
  --dry-run          show what would change; change nothing
  -h, --help         show this help

The registration token is read from stdin (valid for one hour).
EOF
}

# GitHub's runner, pinned to a release at least 7 days old, with its SHA-256
# from the release notes (and the asset digest GitHub records).
RUNNER_VERSION=2.338.0
RUNNER_SHA256=af4b794c1bc41d73d40535e3fe092a39f9679cd8d965954c2aca25a05ca41d32
RUNNER_URL="https://github.com/actions/runner/releases/download/v$RUNNER_VERSION/actions-runner-linux-x64-$RUNNER_VERSION.tar.gz"

# Tests point this at a scratch folder; on a real machine it is /.
ROOT=${DEPLOY_HOST_ROOT:-}

REPO='' LABEL='' RUNNER_USER=gh-runner DRY_RUN=0
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '==> %s\n' "$*"; }
run() {
  if (( DRY_RUN )); then printf '  + %s\n' "$*"; else "$@"; fi
}

while (( $# )); do
  case $1 in
    --repo)    REPO=${2:-}; shift ;;
    --label)   LABEL=${2:-}; shift ;;
    --user)    RUNNER_USER=${2:-}; shift ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    *)         usage >&2; die "unknown argument: $1" ;;
  esac
  shift
done

[[ $REPO =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "--repo must be OWNER/REPO"
[[ $LABEL =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]] || die "--label must be lowercase letters, digits and dashes"
[[ $RUNNER_USER =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "--user is not a valid account name"
if [[ -z $ROOT ]] && (( ! DRY_RUN )) && [[ $(id -u) != 0 ]]; then
  die "run this as root (sudo), on the machine that will run the deploys"
fi

TOKEN=''
if (( ! DRY_RUN )); then
  [[ ! -t 0 ]] || die "pipe the registration token in on stdin (see --help)"
  IFS= read -r TOKEN || true
  TOKEN=${TOKEN//[[:space:]]/}
  [[ $TOKEN =~ ^[A-Za-z0-9]{20,100}$ ]] || die "stdin did not hold a registration token"
fi

SLUG=$(printf '%s' "$REPO" | tr '[:upper:]/' '[:lower:]-')
DIR="$ROOT/opt/claude-harness-runners/$SLUG"
NAME="$(hostname -s)-$LABEL"

log "Packages"
run apt-get install -y --no-install-recommends curl openssh-client

log "Account $RUNNER_USER (no login, no sudo)"
if ! getent passwd "$RUNNER_USER" >/dev/null; then
  run useradd --system --create-home --home-dir "/var/lib/$RUNNER_USER" \
    --shell /usr/sbin/nologin "$RUNNER_USER"
fi

if [[ -e $DIR/.runner ]]; then
  log "Runner already registered in $DIR; leaving it as it is"
  log "To re-register, remove it first: cd $DIR && sudo ./svc.sh uninstall"
  exit 0
fi

log "GitHub runner $RUNNER_VERSION (checksum-checked)"
run mkdir -p "$DIR"
TARBALL="$DIR/runner.tar.gz"
run curl -fsSL --proto '=https' -o "$TARBALL" "$RUNNER_URL"
if (( ! DRY_RUN )); then
  actual=$(sha256sum "$TARBALL" | cut -d' ' -f1)
  if [[ $actual != "$RUNNER_SHA256" ]]; then
    rm -f "$TARBALL"
    die "the runner download does not match its pinned checksum; nothing was installed"
  fi
fi
run tar -xzf "$TARBALL" -C "$DIR"
run rm -f "$TARBALL"
run chown -R "$RUNNER_USER:$RUNNER_USER" "$DIR"

log "Register with $REPO as $NAME, label $LABEL only"
if (( DRY_RUN )); then
  printf '  + runuser -u %s -- %s/config.sh --unattended --url https://github.com/%s --token *** --labels %s --no-default-labels --name %s --replace\n' \
    "$RUNNER_USER" "$DIR" "$REPO" "$LABEL" "$NAME"
else
  (cd "$DIR" && runuser -u "$RUNNER_USER" -- ./config.sh --unattended \
    --url "https://github.com/$REPO" --token "$TOKEN" \
    --labels "$LABEL" --no-default-labels --name "$NAME" --replace)
fi

log "Start it as a service"
if (( DRY_RUN )); then
  printf '  + %s/svc.sh install %s && %s/svc.sh start\n' "$DIR" "$RUNNER_USER" "$DIR"
else
  (cd "$DIR" && ./svc.sh install "$RUNNER_USER" && ./svc.sh start)
fi

log "Done. $NAME takes $REPO's deploy jobs (label $LABEL)."
cat <<EOF
  Check it:   GitHub > $REPO > Settings > Actions > Runners shows $NAME as Idle
  Logs:       journalctl -u 'actions.runner.*$NAME*'
  Remove:     cd $DIR && sudo ./svc.sh uninstall && sudo -u $RUNNER_USER ./config.sh remove
EOF
