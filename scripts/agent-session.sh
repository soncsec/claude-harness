#!/usr/bin/env bash
#
# agent-session.sh - start Claude Code under the project's own GitHub App identity.
#
# Mints a 1-hour GitHub App installation token (outside the sandbox, from a key
# agents cannot read) and starts Claude with it. The agent then pushes branches
# and opens pull requests as <app>[bot], not as you, so you can review and
# approve its work. The token is revoked when Claude exits.
#
# Run this yourself. See docs/setup/github-app.md for the one-time setup.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/agent-session.sh [--check] [-- CLAUDE_ARGS...]

  --check      Check the config and key, mint a token and revoke it again,
               then exit without starting Claude.
  --print-jwt  Print a short-lived app JWT and exit (for tests and debugging).
  -h, --help   Show this help.

Anything after -- is passed to claude, for example:
  scripts/agent-session.sh -- --resume

Config lives in ~/.config/claude-harness/ (override with CLAUDE_HARNESS_CONFIG_DIR):
  agent-app.conf   CLIENT_ID, INSTALLATION_ID, REPO (owner/name), APP_SLUG
  agent-app.pem    the app's private key (chmod 600)
EOF
}

CONFIG_DIR=${CLAUDE_HARNESS_CONFIG_DIR:-$HOME/.config/claude-harness}
CONFIG_FILE="$CONFIG_DIR/agent-app.conf"
KEY_FILE="$CONFIG_DIR/agent-app.pem"
API=${GITHUB_API_URL:-https://api.github.com}
TOKEN_LIFETIME_NOTE="1 hour"

MODE=run
CLAUDE_ARGS=()
while (( $# )); do
  case $1 in
    --check)     MODE=check ;;
    --print-jwt) MODE=jwt ;;
    -h|--help)   usage; exit 0 ;;
    --)          shift; CLAUDE_ARGS=("$@"); break ;;
    *)           usage >&2; printf '\nerror: unknown option: %s\n' "$1" >&2; exit 2 ;;
  esac
  shift
done

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '==> %s\n' "$*"; }

# ---------------------------------------------------------------------------
# Config: plain KEY=VALUE lines, parsed (never sourced) and validated.
# ---------------------------------------------------------------------------

CLIENT_ID='' INSTALLATION_ID='' REPO='' APP_SLUG=''

load_config() {
  local line key value
  [[ -f $CONFIG_FILE ]] || die "missing $CONFIG_FILE (see docs/setup/github-app.md)"
  [[ -f $KEY_FILE ]] || die "missing $KEY_FILE (see docs/setup/github-app.md)"
  [[ $(stat -c %a "$KEY_FILE") == 600 || $(stat -c %a "$KEY_FILE") == 400 ]] \
    || die "$KEY_FILE must be readable only by you: chmod 600 $KEY_FILE"

  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line =~ ^[[:space:]]*(#|$) ]] && continue
    [[ $line =~ ^([A-Z_]+)=(.*)$ ]] || die "bad line in $CONFIG_FILE: $line"
    key=${BASH_REMATCH[1]} value=${BASH_REMATCH[2]}
    case $key in
      CLIENT_ID)       CLIENT_ID=$value ;;
      INSTALLATION_ID) INSTALLATION_ID=$value ;;
      REPO)            REPO=$value ;;
      APP_SLUG)        APP_SLUG=$value ;;
      *)               die "unknown setting in $CONFIG_FILE: $key" ;;
    esac
  done < "$CONFIG_FILE"

  [[ $CLIENT_ID =~ ^[A-Za-z0-9.]+$ ]] || die "CLIENT_ID is missing or malformed"
  [[ $INSTALLATION_ID =~ ^[0-9]+$ ]] || die "INSTALLATION_ID must be a number"
  [[ $REPO =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "REPO must look like owner/name"
  [[ $APP_SLUG =~ ^[a-z0-9-]+$ ]] || die "APP_SLUG must be the app's lowercase name"
}

# ---------------------------------------------------------------------------
# GitHub App authentication
# ---------------------------------------------------------------------------

b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

# A JWT signed with the app key, valid 10 minutes (GitHub's maximum).
make_jwt() {
  local now header payload signature
  now=$(date +%s)
  header=$(printf '{"alg":"RS256","typ":"JWT"}' | b64url)
  payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' $((now - 60)) $((now + 540)) "$CLIENT_ID" \
            | b64url)
  signature=$(printf '%s.%s' "$header" "$payload" \
              | openssl dgst -sha256 -sign "$KEY_FILE" -binary | b64url)
  printf '%s.%s.%s' "$header" "$payload" "$signature"
}

# POST/GET/DELETE to the GitHub API. Credentials go in a header file, never in
# the command line, so they do not show up in the process list.
github_api() {
  local method=$1 path=$2 credential=$3 body=${4:-}
  local args=(-sS -X "$method" -w '\n%{http_code}'
              -H "Accept: application/vnd.github+json"
              -H "X-GitHub-Api-Version: 2022-11-28"
              -H @<(printf 'Authorization: Bearer %s\n' "$credential"))
  [[ -n $body ]] && args+=(-d "$body")
  curl "${args[@]}" "$API$path"
}

json_field() { python3 -c 'import json,sys; print(json.loads(sys.stdin.read())[sys.argv[1]])' "$1"; }

TOKEN='' EXPIRES_AT=''

mint_token() {
  local repo_name=${REPO#*/} body response status jwt
  jwt=$(make_jwt) || die "could not sign with $KEY_FILE; is it the app's private key?"
  # Ask only for this repo and only these permissions, even if the app has more.
  body=$(printf '{"repositories":["%s"],"permissions":{"metadata":"read","contents":"write","pull_requests":"write","issues":"write","checks":"read","statuses":"read","actions":"read"}}' "$repo_name")
  response=$(github_api POST "/app/installations/$INSTALLATION_ID/access_tokens" "$jwt" "$body")
  status=${response##*$'\n'}
  response=${response%$'\n'*}
  [[ $status == 201 ]] || die "GitHub refused to mint a token (HTTP $status): $response"
  TOKEN=$(printf '%s' "$response" | json_field token)
  EXPIRES_AT=$(printf '%s' "$response" | json_field expires_at)
}

revoke_token() {
  [[ -n $TOKEN ]] || return 0
  github_api DELETE /installation/token "$TOKEN" >/dev/null 2>&1 || true
}

BOT_NAME='' BOT_EMAIL=''

bot_identity() {
  local response status id
  BOT_NAME="${APP_SLUG}[bot]"
  response=$(github_api GET "/users/${APP_SLUG}%5Bbot%5D" "$TOKEN")
  status=${response##*$'\n'}
  if [[ $status == 200 ]]; then
    id=$(printf '%s' "${response%$'\n'*}" | json_field id)
    BOT_EMAIL="${id}+${BOT_NAME}@users.noreply.github.com"
  else
    BOT_EMAIL="${BOT_NAME}@users.noreply.github.com"
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  command -v openssl >/dev/null || die "openssl is required"
  command -v curl >/dev/null || die "curl is required"
  command -v python3 >/dev/null || die "python3 is required"
  load_config

  if [[ $MODE == jwt ]]; then
    make_jwt
    echo
    exit 0
  fi

  mint_token
  trap revoke_token EXIT
  bot_identity

  if [[ $MODE == check ]]; then
    log "Config and key OK. Minted a token for $BOT_NAME on $REPO (expires $EXPIRES_AT)."
    log "Revoking it again; nothing else was changed."
    exit 0
  fi

  command -v claude >/dev/null || die "claude (Claude Code) is not on PATH"

  # The agent's GitHub identity: a token for the app, never yours.
  unset GITHUB_TOKEN GH_ENTERPRISE_TOKEN
  export GH_TOKEN="$TOKEN"
  # Commits are labelled as the app.
  export GIT_AUTHOR_NAME="$BOT_NAME" GIT_COMMITTER_NAME="$BOT_NAME"
  export GIT_AUTHOR_EMAIL="$BOT_EMAIL" GIT_COMMITTER_EMAIL="$BOT_EMAIL"
  # git push over HTTPS uses the token, through environment-only git config
  # (nothing is written to your ~/.gitconfig). The empty helper clears others.
  export GIT_CONFIG_COUNT=2
  export GIT_CONFIG_KEY_0="credential.https://github.com.helper" GIT_CONFIG_VALUE_0=""
  export GIT_CONFIG_KEY_1="credential.https://github.com.helper"
  # shellcheck disable=SC2016  # $GH_TOKEN must expand when git runs the helper
  export GIT_CONFIG_VALUE_1='!f() { test "$1" = get && echo username=x-access-token && echo "password=$GH_TOKEN"; }; f'
  export CLAUDE_HARNESS_AGENT_IDENTITY="$BOT_NAME"

  log "Starting Claude as $BOT_NAME on $REPO."
  log "The token lasts $TOKEN_LIFETIME_NOTE (until $EXPIRES_AT). Restart this script to renew it."
  local status=0
  claude "${CLAUDE_ARGS[@]}" || status=$?
  revoke_token
  TOKEN=''
  log "Session ended; the token was revoked."
  exit "$status"
}

main
