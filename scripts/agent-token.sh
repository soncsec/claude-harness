#!/usr/bin/env bash
#
# agent-token.sh - keep the agent's GitHub App tokens fresh, one per project.
#
# A systemd user timer runs `refresh` every 20 minutes. For every configured
# project it mints a new 1-hour token limited to that one repo (outside the
# sandbox, from a key agents cannot read), writes it where gh and git read it
# on every command, and revokes the previous one. Claude sessions in a
# configured project pick up each renewal without restarting.
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
Usage: scripts/agent-token.sh COMMAND [options]

Commands:
  configure DIR     Set up the project in DIR: find its GitHub repo, start
                    keeping its tokens, and write DIR/.claude/settings.local.json
                    (personal, never committed) so Claude sessions there act as the app.
                    Also links ~/.local/bin/hclaude: start sessions with it to hide
                    the API token from the agent.
  refresh           Renew the token of every configured project (the timer runs this).
  check             Mint and revoke a token for every configured project.
  status            Show the timer and when each project's token expires.
  forget REPO       Stop keeping a token for owner/name and revoke its current one.
  install-timer     Renew every 20 minutes in the background (systemd user timer).
  uninstall-timer   Stop and remove the timer.
  jwt               Print a short-lived app JWT (for tests and debugging).

Options:
  --repo OWNER/NAME  With configure: use this repo instead of the git remote.
                     With refresh: renew only this repo.
  --dry-run          With configure or install-timer: show what would happen.
EOF
}

DRY_RUN=0
COMMAND=''
TARGET=''
ONLY_REPO=''
# Where configure links the hclaude launcher.
BIN_DIR=${CLAUDE_HARNESS_BIN_DIR:-$HOME/.local/bin}
while (( $# )); do
  case $1 in
    --dry-run) DRY_RUN=1 ;;
    --repo)    shift; ONLY_REPO=${1:-} ;;
    -h|--help) usage; exit 0 ;;
    -*)        usage >&2; printf '\nerror: unknown option: %s\n' "$1" >&2; exit 2 ;;
    *)
      if [[ -z $COMMAND ]]; then COMMAND=$1; else TARGET=$1; fi
      ;;
  esac
  shift
done
[[ -z $ONLY_REPO ]] || valid_repo "$ONLY_REPO" || die "--repo must look like owner/name"

repos_or_hint() {
  local repos
  repos=$(registered_repos)
  [[ -n $repos ]] || die "no projects configured yet: run 'scripts/agent-token.sh configure <project dir>'"
  printf '%s\n' "$repos"
}

legacy_notice() {
  if [[ -f $LEGACY_TOKEN_DIR/hosts.yml ]]; then
    log "Note: $LEGACY_TOKEN_DIR is from the old single-repo setup. Run 'configure' again"
    log "for that project, then remove the REPO= line from $CONFIG_FILE and delete that folder."
  fi
}

cmd_refresh() {
  local repo repos failed=0
  if [[ -n $ONLY_REPO ]]; then
    refresh_repo "$ONLY_REPO" || failed=1
  else
    repos=$(repos_or_hint)
    while IFS= read -r repo; do
      refresh_repo "$repo" || failed=1
    done <<< "$repos"
    legacy_notice
  fi
  (( failed == 0 )) || die "some tokens could not be renewed; see the errors above"
}

cmd_check() {
  local repo repos failed=0
  repos=$(repos_or_hint)
  while IFS= read -r repo; do
    if mint_token "$repo"; then
      revoke_token
      log "$repo: OK (minted and revoked a token for $BOT_NAME)."
    else
      failed=1
    fi
  done <<< "$repos"
  (( failed == 0 )) || die "some projects failed the check; see the errors above"
}

cmd_status() {
  local repo dir
  if command -v systemctl >/dev/null; then
    systemctl --user list-timers "$UNIT.timer" --no-pager 2>/dev/null || true
  fi
  log "Now: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  while IFS= read -r repo; do
    [[ -n $repo ]] || continue
    dir=$(token_dir_for "$repo")
    if [[ -f $dir/expires_at ]]; then
      log "$repo: token expires $(cat "$dir/expires_at")"
    else
      log "$repo: no token yet"
    fi
  done <<< "$(registered_repos)"
  legacy_notice
}

cmd_configure() {
  local project repo settings
  [[ -n $TARGET && -d $TARGET ]] || die "usage: agent-token.sh configure PROJECT_DIR [--repo owner/name]"
  project=$(cd "$TARGET" && pwd)
  repo=${ONLY_REPO:-$(repo_from_remote "$project")}
  [[ -n $repo ]] \
    || die "$project has no GitHub 'origin' remote; pass --repo owner/name"
  settings="$project/.claude/settings.local.json"
  bot_identity
  agent_env "$repo"

  local pairs=() key
  for key in "${!AGENT_ENV[@]}"; do
    pairs+=("$key=${AGENT_ENV[$key]}")
  done

  local session_settings launcher
  session_settings=$(session_settings_for "$repo")
  launcher="$BIN_DIR/hclaude"

  if (( DRY_RUN )); then
    log "Would keep a token for $repo and add to $settings under \"env\":"
    printf '  %s\n' "${pairs[@]}" | sort
    log "Would write $session_settings (masks the API token in hclaude sessions)"
    log "Would link $launcher to $HARNESS_DIR/scripts/hclaude.sh"
    return
  fi

  register_repo "$repo"
  refresh_repo "$repo" || die "could not get a token for $repo; settings were not changed"

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
  log "Wrote $settings: Claude sessions in $project now act as $BOT_NAME on $repo."

  write_session_settings "$repo"
  mkdir -p "$BIN_DIR"
  ln -sfn "$HARNESS_DIR/scripts/hclaude.sh" "$launcher"
  log "Start sessions there with hclaude ($launcher): it hides the API token from the agent."
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) printf 'warning: %s is not on your PATH; add it, or run %s directly\n' "$BIN_DIR" "$launcher" >&2 ;;
  esac
  if git -C "$project" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
     && ! git -C "$project" check-ignore -q "$settings"; then
    printf 'warning: %s is not git-ignored; add .claude/settings.local.json to .gitignore\n' \
      "$settings" >&2
  fi
}

cmd_forget() {
  local repo=$TARGET dir
  valid_repo "$repo" || die "usage: agent-token.sh forget owner/name"
  dir=$(token_dir_for "$repo")
  revoke_token "$(current_token "$dir")"
  revoke_token "$(current_push_token "$dir")"
  rm -rf "$dir"
  rm -f "$(session_settings_for "$repo")"
  unregister_repo "$repo"
  log "$repo: token revoked and no longer renewed."
  [[ $repo != "$LEGACY_REPO" ]] || log "It is also named in REPO= in $CONFIG_FILE: remove that line too."
}

service_unit() {
  cat <<EOF
[Unit]
Description=Refresh the claude-harness agent's GitHub App tokens

[Service]
Type=oneshot
ExecStart=$HARNESS_DIR/scripts/agent-token.sh refresh
EOF
}

timer_unit() {
  cat <<EOF
[Unit]
Description=Refresh the claude-harness agent's GitHub App tokens every 20 minutes

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
  log "Timer installed. If you move this repo, run install-timer again (it stores the path)."
}

cmd_uninstall_timer() {
  systemctl --user disable --now "$UNIT.timer" 2>/dev/null || true
  rm -f "$UNIT_DIR/$UNIT.service" "$UNIT_DIR/$UNIT.timer"
  systemctl --user daemon-reload 2>/dev/null || true
  log "Timer removed. Current tokens expire within the hour."
}

main() {
  case $COMMAND in
    check|refresh|configure|forget|status|jwt) require_tools; load_config ;;
  esac
  case $COMMAND in
    configure)       cmd_configure ;;
    refresh)         cmd_refresh ;;
    check)           cmd_check ;;
    status)          cmd_status ;;
    forget)          cmd_forget ;;
    install-timer)   cmd_install_timer ;;
    uninstall-timer) cmd_uninstall_timer ;;
    jwt)             make_jwt; echo ;;
    '')              usage >&2; exit 2 ;;
    *)               usage >&2; printf '\nerror: unknown command: %s\n' "$COMMAND" >&2; exit 2 ;;
  esac
}

main
