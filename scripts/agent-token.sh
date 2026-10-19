#!/usr/bin/env bash
#
# agent-token.sh - keep the agent's GitHub App token fresh, and wire projects to it.
#
# A systemd user timer runs `refresh` every 20 minutes. Each run mints a new
# 1-hour token from the app key (outside the sandbox) and writes it where gh
# and git read it on every command, then revokes the previous token. Claude
# sessions in a configured project pick up each renewal without restarting.
#
# Run it yourself. One-time setup: docs/setup/github-app.md.

set -euo pipefail

HARNESS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/github-app.sh
source "$HARNESS_DIR/scripts/lib/github-app.sh"

UNIT=claude-harness-agent-token
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user

usage() {
  cat <<'EOF'
Usage: scripts/agent-token.sh COMMAND [--dry-run]

Commands:
  check             Mint a token and revoke it again: proves the key and config work.
  refresh           Mint a fresh token into ~/.cache/claude-harness/gh/ and revoke
                    the previous one. The timer runs this every 20 minutes.
  configure DIR     Wire the project in DIR so Claude sessions there act as the app
                    (writes DIR/.claude/settings.local.json: personal, never committed).
  install-timer     Refresh in the background every 20 minutes (systemd user timer).
  uninstall-timer   Stop and remove the timer.
  status            Show the timer and when the current token expires.
  jwt               Print a short-lived app JWT (for tests and debugging).

  --dry-run         With configure or install-timer: show what would be written.
EOF
}

DRY_RUN=0
COMMAND=''
TARGET=''
while (( $# )); do
  case $1 in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    -*)        usage >&2; printf '\nerror: unknown option: %s\n' "$1" >&2; exit 2 ;;
    *)
      if [[ -z $COMMAND ]]; then COMMAND=$1; else TARGET=$1; fi
      ;;
  esac
  shift
done

cmd_check() {
  mint_token
  bot_identity
  revoke_token
  log "Config and key OK: minted and revoked a token for $BOT_NAME on $REPO."
}

cmd_refresh() {
  local previous
  previous=$(current_token)
  mint_token
  write_token_files
  [[ -n $previous && $previous != "$TOKEN" ]] && revoke_token "$previous"
  log "New token for $BOT_NAME on $REPO, valid until $EXPIRES_AT."
}

cmd_configure() {
  local project settings
  [[ -n $TARGET && -d $TARGET ]] || die "usage: agent-token.sh configure PROJECT_DIR"
  project=$(cd "$TARGET" && pwd)
  settings="$project/.claude/settings.local.json"
  bot_identity
  agent_env

  local pairs=() key
  for key in "${!AGENT_ENV[@]}"; do
    pairs+=("$key=${AGENT_ENV[$key]}")
  done

  if (( DRY_RUN )); then
    log "Would add to $settings under \"env\":"
    printf '  %s\n' "${pairs[@]}" | sort
    return
  fi

  mkdir -p "$project/.claude"
  # Merge into any existing local settings, keeping everything else as it is.
  python3 - "$settings" "${pairs[@]}" <<'PY'
import json, os, sys
path, pairs = sys.argv[1], sys.argv[2:]
data = {}
if os.path.exists(path):
    with open(path) as fh:
        data = json.load(fh)
env = data.setdefault("env", {})
for pair in pairs:
    key, value = pair.split("=", 1)
    env[key] = value
with open(path, "w") as fh:
    json.dump(data, fh, indent=2, sort_keys=True)
    fh.write("\n")
PY
  chmod 600 "$settings"
  log "Wrote $settings: Claude sessions in $project now act as $BOT_NAME."
  if git -C "$project" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
     && ! git -C "$project" check-ignore -q "$settings"; then
    printf 'warning: %s is not git-ignored; add .claude/settings.local.json to .gitignore\n' \
      "$settings" >&2
  fi
  [[ -f $TOKEN_DIR/hosts.yml ]] || log "No token yet: run 'agent-token.sh refresh' or install the timer."
}

service_unit() {
  cat <<EOF
[Unit]
Description=Refresh the claude-harness agent's GitHub App token

[Service]
Type=oneshot
ExecStart=$HARNESS_DIR/scripts/agent-token.sh refresh
EOF
}

timer_unit() {
  cat <<EOF
[Unit]
Description=Refresh the claude-harness agent's GitHub App token every 20 minutes

[Timer]
# Shortly after you log in, then every 20 minutes (tokens last 60).
OnStartupSec=30s
OnCalendar=*:0/20
# Catch up straight away after the machine was asleep or off.
Persistent=true
AccuracySec=1min

[Install]
WantedBy=timers.target
EOF
}

cmd_install_timer() {
  command -v systemctl >/dev/null || die "systemd is not available; use scripts/agent-session.sh instead"
  if (( DRY_RUN )); then
    log "Would write $UNIT_DIR/$UNIT.service:"
    service_unit
    echo
    log "Would write $UNIT_DIR/$UNIT.timer:"
    timer_unit
    return
  fi
  mkdir -p "$UNIT_DIR"
  service_unit > "$UNIT_DIR/$UNIT.service"
  timer_unit > "$UNIT_DIR/$UNIT.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT.timer"
  cmd_refresh
  log "Timer installed. If you move this repo, run install-timer again (it stores the path)."
}

cmd_uninstall_timer() {
  systemctl --user disable --now "$UNIT.timer" 2>/dev/null || true
  rm -f "$UNIT_DIR/$UNIT.service" "$UNIT_DIR/$UNIT.timer"
  systemctl --user daemon-reload 2>/dev/null || true
  log "Timer removed. The last token expires within the hour."
}

cmd_status() {
  if command -v systemctl >/dev/null; then
    systemctl --user list-timers "$UNIT.timer" --no-pager 2>/dev/null || true
  fi
  if [[ -f $TOKEN_DIR/expires_at ]]; then
    log "Current token expires: $(cat "$TOKEN_DIR/expires_at") (now: $(date -u +%Y-%m-%dT%H:%M:%SZ))"
  else
    log "No token on disk yet."
  fi
}

main() {
  case $COMMAND in
    check|refresh|configure|install-timer|jwt) require_tools; load_config ;;
  esac
  case $COMMAND in
    check)           cmd_check ;;
    refresh)         cmd_refresh ;;
    configure)       cmd_configure ;;
    install-timer)   cmd_install_timer ;;
    uninstall-timer) cmd_uninstall_timer ;;
    status)          cmd_status ;;
    jwt)             make_jwt; echo ;;
    '')              usage >&2; exit 2 ;;
    *)               usage >&2; printf '\nerror: unknown command: %s\n' "$COMMAND" >&2; exit 2 ;;
  esac
}

main
