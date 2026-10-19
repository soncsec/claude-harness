#!/usr/bin/env bash
#
# agent-session.sh - start Claude as the project's GitHub App, without systemd.
#
# Fallback for machines without the background timer (see agent-token.sh).
# It refreshes the token now, keeps refreshing it every 20 minutes while
# Claude runs, and starts Claude with the same wiring `agent-token.sh
# configure` writes into settings, so sessions never expire.
#
# With the timer installed and the project configured, just run `claude`.

set -euo pipefail

HARNESS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/github-app.sh
source "$HARNESS_DIR/scripts/lib/github-app.sh"

REFRESH_SECONDS=1200

usage() {
  cat <<'EOF'
Usage: scripts/agent-session.sh [-- CLAUDE_ARGS...]

Starts Claude acting as the project's GitHub App and keeps its token fresh
for as long as Claude runs. Anything after -- goes to claude, for example:
  scripts/agent-session.sh -- --resume

Prefer the background timer: scripts/agent-token.sh install-timer, then
scripts/agent-token.sh configure . and plain `claude`.
EOF
}

CLAUDE_ARGS=()
while (( $# )); do
  case $1 in
    -h|--help) usage; exit 0 ;;
    --)        shift; CLAUDE_ARGS=("$@"); break ;;
    *)         usage >&2; printf '\nerror: unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

main() {
  require_tools
  load_config
  command -v claude >/dev/null || die "claude (Claude Code) is not on PATH"

  "$HARNESS_DIR/scripts/agent-token.sh" refresh
  bot_identity
  agent_env

  # Keep the token fresh in the background while Claude runs.
  ( while sleep "$REFRESH_SECONDS"; do
      "$HARNESS_DIR/scripts/agent-token.sh" refresh >/dev/null 2>&1 || true
    done ) &
  local refresher=$!
  trap 'kill "$refresher" 2>/dev/null || true' EXIT

  local key
  for key in "${!AGENT_ENV[@]}"; do
    export "$key=${AGENT_ENV[$key]}"
  done
  unset GH_TOKEN GITHUB_TOKEN GH_ENTERPRISE_TOKEN  # only the app's token, never yours

  log "Starting Claude as $BOT_NAME on $REPO; the token renews every 20 minutes."
  local status=0
  claude "${CLAUDE_ARGS[@]}" || status=$?
  exit "$status"
}

main
