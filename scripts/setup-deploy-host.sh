#!/usr/bin/env bash
#
# setup-deploy-host.sh - prepare a Debian 13 server to receive deploys of one app.
#
# Run it as root on each server (staging and production), with the command
# scripts/setup-deploy.sh prints for that server. Safe to re-run: it updates the
# config, the unit and the deploy key in place.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sudo scripts/setup-deploy-host.sh [options] --app NAME --image IMAGE --ci-key KEY

  --app NAME           the app's name: its systemd unit, data folder and env file
  --image IMAGE        the only image this server accepts, e.g. ghcr.io/owner/repo
  --ci-key KEY         the public key CI deploys with (from setup-deploy.sh)
  --port PORT          the port the app listens on inside and on 127.0.0.1 (default 8000)
  --health-path PATH   answers 2xx when the app is up (default /health)
  --pull-user LOGIN    GitHub login for --pull-token-file (private images)
  --pull-token-file F  a file holding a token that can only read packages
  --user USER          the account that runs the app (default deploy)
  --dry-run            show what would change; change nothing
  -h, --help           show this help

What it sets up:
  - podman (rootless), uidmap, passt, dbus-user-session and curl
  - the USER account, with lingering so its services run without a login
  - /usr/local/libexec/claude-harness/deploy-receive.sh and
    /etc/claude-harness/deploy-NAME.conf, both root-owned: CI's key can only
    run that script, which only accepts an exact digest of IMAGE
  - ~USER/.config/containers/systemd/NAME.container (the systemd unit)
  - ~USER/NAME.env (app settings, kept if it exists) and ~USER/NAME-data/
  - the CI key in ~USER/.ssh/authorized_keys, restricted to the deploy script
EOF
}

HARNESS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
HOST_FILES="$HARNESS_DIR/core/deploy/host"
# Tests point this at a scratch folder; on a real server it is /.
ROOT=${DEPLOY_HOST_ROOT:-}

APP='' IMAGE='' CI_KEY='' PORT=8000 HEALTH_PATH=/health
PULL_USER='' PULL_TOKEN_FILE='' DEPLOY_USER=deploy DRY_RUN=0

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '==> %s\n' "$*"; }
run() {
  if (( DRY_RUN )); then printf '  + %s\n' "$*"; else "$@"; fi
}

while (( $# )); do
  case $1 in
    --app)             APP=${2:-}; shift ;;
    --image)           IMAGE=${2:-}; shift ;;
    --ci-key)          CI_KEY=${2:-}; shift ;;
    --port)            PORT=${2:-}; shift ;;
    --health-path)     HEALTH_PATH=${2:-}; shift ;;
    --pull-user)       PULL_USER=${2:-}; shift ;;
    --pull-token-file) PULL_TOKEN_FILE=${2:-}; shift ;;
    --user)            DEPLOY_USER=${2:-}; shift ;;
    --dry-run)         DRY_RUN=1 ;;
    -h|--help)         usage; exit 0 ;;
    *)                 usage >&2; die "unknown argument: $1" ;;
  esac
  shift
done

[[ $APP =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || die "--app must be lowercase letters, digits and dashes"
[[ $IMAGE =~ ^[a-z0-9.-]+(/[a-z0-9._-]+)+$ ]] || die "--image must look like ghcr.io/owner/repo (lowercase)"
if [[ ! $PORT =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then die "--port must be 1-65535"; fi
[[ $HEALTH_PATH =~ ^/[A-Za-z0-9._~/-]*$ ]] || die "--health-path must start with / and have no spaces"
[[ $DEPLOY_USER =~ ^[a-z_][a-z0-9_-]{0,31}$ ]] || die "--user is not a valid account name"
# Only the key type and data: we write the options and comment ourselves.
read -r key_type key_data _ <<<"$CI_KEY" || true
[[ ${key_type:-} =~ ^(ssh-ed25519|ecdsa-sha2-nistp256|ecdsa-sha2-nistp384|ssh-rsa)$ \
   && ${key_data:-} =~ ^[A-Za-z0-9+/]+=*$ ]] || die "--ci-key must be an SSH public key line"
if [[ -n $PULL_TOKEN_FILE ]]; then
  [[ -n $PULL_USER ]] || die "--pull-token-file needs --pull-user"
  [[ -r $PULL_TOKEN_FILE ]] || die "cannot read $PULL_TOKEN_FILE"
fi
if [[ -z $ROOT ]] && (( ! DRY_RUN )) && [[ $(id -u) != 0 ]]; then
  die "run this as root (sudo), on the server"
fi

LIBEXEC="$ROOT/usr/local/libexec/claude-harness"
RECEIVE="$LIBEXEC/deploy-receive.sh"
RECEIVE_ON_HOST="/usr/local/libexec/claude-harness/deploy-receive.sh"
CONF_ON_HOST="/etc/claude-harness/deploy-$APP.conf"
CONF="$ROOT$CONF_ON_HOST"
MARKER="claude-harness-deploy-$APP"

log "Packages"
run apt-get install -y --no-install-recommends podman uidmap passt dbus-user-session curl

log "Account $DEPLOY_USER"
if ! getent passwd "$DEPLOY_USER" >/dev/null; then
  run useradd --create-home --shell /bin/bash "$DEPLOY_USER"
fi
run loginctl enable-linger "$DEPLOY_USER"
if (( DRY_RUN )) && ! getent passwd "$DEPLOY_USER" >/dev/null; then
  HOME_DIR="/home/$DEPLOY_USER"
else
  HOME_DIR=$(getent passwd "$DEPLOY_USER" | cut -d: -f6)
fi
HOME_DIR="$ROOT$HOME_DIR"

log "Deploy script and config (root-owned)"
run mkdir -p "$LIBEXEC" "$(dirname "$CONF")"
run install -m 0755 "$HOST_FILES/deploy-receive.sh" "$RECEIVE"
config="APP=$APP
IMAGE=$IMAGE
HEALTH_URL=http://127.0.0.1:$PORT$HEALTH_PATH
HEALTH_TIMEOUT=60
HEALTH_INTERVAL=2"
if (( DRY_RUN )); then
  printf '  + write %s:\n      %s\n' "$CONF" "${config//$'\n'/$'\n      '}"
else
  printf '%s\n' "$config" > "$CONF"
  chmod 0644 "$CONF"
fi
run chown root:root "$RECEIVE" "$CONF"

log "Systemd unit, settings and data folder"
UNIT_DIR="$HOME_DIR/.config/containers/systemd"
run mkdir -p "$UNIT_DIR" "$HOME_DIR/$APP-data" "$HOME_DIR/.ssh"
unit=$(sed -e "s/{{APP}}/$APP/g" -e "s/{{PORT}}/$PORT/g" "$HOST_FILES/app.container")
if (( DRY_RUN )); then
  printf '  + write %s\n' "$UNIT_DIR/$APP.container"
else
  printf '%s\n' "$unit" > "$UNIT_DIR/$APP.container"
  if [[ ! -e $HOME_DIR/$APP.env ]]; then
    printf '# Settings and secrets for %s. Never commit these.\n' "$APP" > "$HOME_DIR/$APP.env"
  fi
  chmod 0600 "$HOME_DIR/$APP.env"
fi

log "CI deploy key (restricted to the deploy script)"
AUTH_KEYS="$HOME_DIR/.ssh/authorized_keys"
entry="restrict,command=\"$RECEIVE_ON_HOST $CONF_ON_HOST\" $key_type $key_data $MARKER"
if (( DRY_RUN )); then
  printf '  + set the %s line in %s:\n      %s\n' "$MARKER" "$AUTH_KEYS" "$entry"
else
  touch "$AUTH_KEYS"
  # Replace this app's previous key, keep every other line.
  { grep -v " $MARKER\$" "$AUTH_KEYS" || true; printf '%s\n' "$entry"; } > "$AUTH_KEYS.new"
  mv "$AUTH_KEYS.new" "$AUTH_KEYS"
  chmod 0700 "$HOME_DIR/.ssh"
  chmod 0600 "$AUTH_KEYS"
fi

if [[ -n $PULL_TOKEN_FILE ]]; then
  log "Registry login for private images"
  AUTH_JSON="$HOME_DIR/.config/containers/auth.json"
  if (( DRY_RUN )); then
    printf '  + write %s (ghcr.io login for %s)\n' "$AUTH_JSON" "$PULL_USER"
  else
    token=$(tr -d '[:space:]' < "$PULL_TOKEN_FILE")
    auth=$(printf '%s:%s' "$PULL_USER" "$token" | base64 -w0)
    (umask 077; printf '{"auths": {"ghcr.io": {"auth": "%s"}}}\n' "$auth" > "$AUTH_JSON")
  fi
fi

run chown -R "$DEPLOY_USER:$DEPLOY_USER" "$HOME_DIR/.config" "$HOME_DIR/.ssh" \
  "$HOME_DIR/$APP-data" "$HOME_DIR/$APP.env"
run systemctl --user --machine="$DEPLOY_USER@" daemon-reload

log "Done. $APP starts with its first deploy."
cat <<EOF
  App settings:   $HOME_DIR/$APP.env (edit as root or $DEPLOY_USER)
  App data:       $HOME_DIR/$APP-data (mounted at /data in the container)
  Listens on:     127.0.0.1:$PORT; add a reverse proxy for outside access
  Logs:           sudo -u $DEPLOY_USER journalctl --user -u $APP
EOF
