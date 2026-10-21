#!/usr/bin/env bash
#
# deploy-receive.sh - the only command CI's deploy key may run on a server.
#
# sshd runs it as the `deploy` user (a forced command in authorized_keys, set up
# by scripts/setup-deploy-host.sh), with the image CI asked for in
# SSH_ORIGINAL_COMMAND. It accepts only an exact digest of the configured image,
# switches the app to it, and rolls back to the previous release if the new one
# fails its health check.
#
# Usage (from authorized_keys): deploy-receive.sh /etc/claude-harness/deploy-APP.conf
#
# The config file is root-owned, one KEY=VALUE per line:
#   APP=notes                                  systemd unit is APP.service
#   IMAGE=ghcr.io/owner/repo                   the only image that may be deployed
#   HEALTH_URL=http://127.0.0.1:8000/health    must answer 2xx once the app is up
#   HEALTH_TIMEOUT=60                          seconds to wait for it (optional)
#   HEALTH_INTERVAL=2                          seconds between tries (optional)

set -euo pipefail

die() { printf 'deploy: %s\n' "$*" >&2; exit 1; }
log() { printf 'deploy: %s\n' "$*"; }

CONFIG=${1:-}
[[ -n $CONFIG && -f $CONFIG ]] || die "config file missing: ${CONFIG:-<none>}"

APP='' IMAGE='' HEALTH_URL='' HEALTH_TIMEOUT=60 HEALTH_INTERVAL=2
# Parse, never source: the file sets data, not code.
while IFS= read -r line || [[ -n $line ]]; do
  [[ -z $line || $line == \#* ]] && continue
  key=${line%%=*}
  value=${line#*=}
  case $key in
    APP)             [[ $value =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || die "bad APP in $CONFIG"; APP=$value ;;
    IMAGE)           [[ $value =~ ^[a-z0-9.-]+(/[a-z0-9._-]+)+$ ]] || die "bad IMAGE in $CONFIG"; IMAGE=$value ;;
    HEALTH_URL)      [[ $value =~ ^https?://[^[:space:]]+$ ]] || die "bad HEALTH_URL in $CONFIG"; HEALTH_URL=$value ;;
    HEALTH_TIMEOUT)  [[ $value =~ ^[0-9]+$ ]] || die "bad HEALTH_TIMEOUT in $CONFIG"; HEALTH_TIMEOUT=$value ;;
    HEALTH_INTERVAL) [[ $value =~ ^[0-9]+$ ]] || die "bad HEALTH_INTERVAL in $CONFIG"; HEALTH_INTERVAL=$value ;;
    *)               die "unknown setting $key in $CONFIG" ;;
  esac
done < "$CONFIG"
[[ -n $APP && -n $IMAGE && -n $HEALTH_URL ]] || die "$CONFIG needs APP, IMAGE and HEALTH_URL"

# Only "<IMAGE>@sha256:<64 hex>", exactly: no tags, no other images, no extra words.
requested=${SSH_ORIGINAL_COMMAND:-}
digest=${requested#"$IMAGE"@sha256:}
if [[ $requested != "$IMAGE@sha256:$digest" || ! $digest =~ ^[0-9a-f]{64}$ ]]; then
  die "refused: expected $IMAGE@sha256:<digest>, got '${requested:0:120}'"
fi

export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
# The registry login setup-deploy-host.sh wrote (private images only).
if [[ -f $HOME/.config/containers/auth.json ]]; then
  export REGISTRY_AUTH_FILE=$HOME/.config/containers/auth.json
fi

# One deploy at a time on this host for this app.
exec 9>"$HOME/.deploy-$APP.lock"
flock -w 600 9 || die "another deploy of $APP is still running"

CURRENT="localhost/$APP:current"
PREVIOUS="localhost/$APP:previous"

healthy() {
  local deadline=$(( SECONDS + HEALTH_TIMEOUT ))
  while true; do
    curl -fsS -o /dev/null --max-time 5 "$HEALTH_URL" 2>/dev/null && return 0
    (( SECONDS < deadline )) || return 1
    sleep "$HEALTH_INTERVAL"
  done
}

log "pulling $requested"
podman pull --quiet "$requested" >/dev/null

had_previous=0
if podman image exists "$CURRENT"; then
  podman tag "$CURRENT" "$PREVIOUS"
  had_previous=1
fi
podman tag "$requested" "$CURRENT"

log "restarting $APP.service"
systemctl --user restart "$APP.service" || true

if healthy; then
  log "deployed $requested ($APP is healthy)"
  podman image prune --force >/dev/null 2>&1 || true
  exit 0
fi

log "health check failed for $requested"
if (( had_previous )); then
  podman tag "$PREVIOUS" "$CURRENT"
  systemctl --user restart "$APP.service" || true
  if healthy; then
    die "rolled back to the previous release; $APP is healthy again"
  fi
  die "rolled back to the previous release, but $APP is still unhealthy"
fi
systemctl --user stop "$APP.service" || true
die "first deploy failed and there is no previous release; $APP is stopped"
