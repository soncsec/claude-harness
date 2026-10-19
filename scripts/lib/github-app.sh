# shellcheck shell=bash
#
# Shared GitHub App helpers for agent-token.sh and agent-session.sh.
# Source this file; it defines functions and paths, and runs nothing.
#
# Files (override the folders with the environment variables in brackets):
#   ~/.config/claude-harness/agent-app.conf   app settings     [CLAUDE_HARNESS_CONFIG_DIR]
#   ~/.config/claude-harness/agent-app.pem    app private key  (agents cannot read this folder)
#   ~/.cache/claude-harness/gh/hosts.yml      current token, in gh's format [CLAUDE_HARNESS_TOKEN_DIR]

HARNESS_CONFIG_DIR=${CLAUDE_HARNESS_CONFIG_DIR:-$HOME/.config/claude-harness}
CONFIG_FILE="$HARNESS_CONFIG_DIR/agent-app.conf"
KEY_FILE="$HARNESS_CONFIG_DIR/agent-app.pem"
TOKEN_DIR=${CLAUDE_HARNESS_TOKEN_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/claude-harness/gh}
GITHUB_API=${GITHUB_API_URL:-https://api.github.com}

CLIENT_ID='' INSTALLATION_ID='' REPO='' APP_SLUG=''
TOKEN='' EXPIRES_AT='' BOT_NAME='' BOT_EMAIL=''

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '==> %s\n' "$*"; }

require_tools() {
  local tool
  for tool in openssl curl python3; do
    command -v "$tool" >/dev/null || die "$tool is required"
  done
}

# Config: plain KEY=VALUE lines, parsed (never sourced) and validated.
load_config() {
  local line key value mode
  [[ -f $CONFIG_FILE ]] || die "missing $CONFIG_FILE (see docs/setup/github-app.md)"
  [[ -f $KEY_FILE ]] || die "missing $KEY_FILE (see docs/setup/github-app.md)"
  mode=$(stat -c %a "$KEY_FILE")
  [[ $mode == 600 || $mode == 400 ]] \
    || die "$KEY_FILE must be readable only by you: chmod 600 $KEY_FILE"

  while IFS= read -r line || [[ -n $line ]]; do
    line=${line%$'\r'}  # tolerate Windows line endings
    [[ $line =~ ^[[:space:]]*(#|$) ]] && continue
    [[ $line =~ ^[[:space:]]*([A-Z_]+)[[:space:]]*=[[:space:]]*(.*[^[:space:]])?[[:space:]]*$ ]] \
      || die "bad line in $CONFIG_FILE: $line"
    key=${BASH_REMATCH[1]} value=${BASH_REMATCH[2]:-}
    # tolerate one pair of surrounding quotes
    if [[ $value =~ ^\"(.*)\"$ || $value =~ ^\'(.*)\'$ ]]; then
      value=${BASH_REMATCH[1]}
    fi
    case $key in
      CLIENT_ID)       CLIENT_ID=$value ;;
      INSTALLATION_ID) INSTALLATION_ID=$value ;;
      REPO)            REPO=$value ;;
      APP_SLUG)        APP_SLUG=$value ;;
      *)               die "unknown setting in $CONFIG_FILE: $key" ;;
    esac
  done < "$CONFIG_FILE"

  # None of these values are secret, so errors show what was found.
  [[ $CLIENT_ID =~ ^[A-Za-z0-9.]+$ ]] \
    || die "CLIENT_ID '$CLIENT_ID' is not valid: copy the Client ID (starts with Iv) from the app's settings page"
  [[ $INSTALLATION_ID =~ ^[0-9]+$ ]] \
    || die "INSTALLATION_ID '$INSTALLATION_ID' must be a number (end of the URL at github.com/settings/installations)"
  [[ $REPO =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] \
    || die "REPO '$REPO' must look like owner/name"
  [[ $APP_SLUG =~ ^[a-z0-9-]+$ ]] \
    || die "APP_SLUG '$APP_SLUG' must be the app's lowercase name, as in its URL"
  BOT_NAME="${APP_SLUG}[bot]"
}

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

# Call the GitHub API; prints the body, a newline, then the HTTP status.
# The credential reaches curl through its config on stdin (--config -): never
# on the command line (where `ps` shows it) and never in a file on disk.
github_api() {
  local method=$1 path=$2 credential=${3:-} body=${4:-}
  local args=(-sS -X "$method" -w '\n%{http_code}'
              -H "Accept: application/vnd.github+json"
              -H "X-GitHub-Api-Version: 2022-11-28"
              --config -)
  [[ -n $body ]] && args+=(-d "$body")
  local config=''
  [[ -n $credential ]] && config="header = \"Authorization: Bearer $credential\""
  printf '%s\n' "$config" | curl "${args[@]}" "$GITHUB_API$path"
}

json_field() { python3 -c 'import json,sys; print(json.loads(sys.stdin.read())[sys.argv[1]])' "$1"; }

# Mint an installation token, narrowed to this one repo and these permissions
# even if the app was given more. Sets TOKEN and EXPIRES_AT.
mint_token() {
  local repo_name=${REPO#*/} body response status jwt
  jwt=$(make_jwt) || die "could not sign with $KEY_FILE; is it the app's private key?"
  body=$(printf '{"repositories":["%s"],"permissions":{"metadata":"read","contents":"write","pull_requests":"write","issues":"write","checks":"read","statuses":"read","actions":"read"}}' "$repo_name")
  response=$(github_api POST "/app/installations/$INSTALLATION_ID/access_tokens" "$jwt" "$body") \
    || die "could not reach GitHub to mint a token (curl failed; see the message above)"
  status=${response##*$'\n'}
  response=${response%$'\n'*}
  [[ $status == 201 ]] || die "GitHub refused to mint a token (HTTP $status): $response"
  TOKEN=$(printf '%s' "$response" | json_field token)
  EXPIRES_AT=$(printf '%s' "$response" | json_field expires_at)
}

revoke_token() {
  local token=${1:-$TOKEN}
  [[ -n $token ]] || return 0
  github_api DELETE /installation/token "$token" >/dev/null 2>&1 || true
}

# The app's bot user, for labelling commits. Public data; no token needed.
bot_identity() {
  local response status id
  response=$(github_api GET "/users/${APP_SLUG}%5Bbot%5D") || response=$'\n000'
  status=${response##*$'\n'}
  if [[ $status == 200 ]]; then
    id=$(printf '%s' "${response%$'\n'*}" | json_field id)
    BOT_EMAIL="${id}+${BOT_NAME}@users.noreply.github.com"
  else
    BOT_EMAIL="${BOT_NAME}@users.noreply.github.com"
  fi
}

# The token currently on disk, if any (to revoke it once replaced).
current_token() {
  [[ -f $TOKEN_DIR/hosts.yml ]] || return 0
  sed -n 's/^ *oauth_token: *//p' "$TOKEN_DIR/hosts.yml" | head -n1
}

# Write TOKEN where gh (and git, through gh) read it on every command, so a
# running Claude session picks up each renewal without restarting.
write_token_files() {
  local tmp
  ( umask 077; mkdir -p "$TOKEN_DIR" )
  chmod 700 "$TOKEN_DIR"
  tmp=$(umask 077; mktemp "$TOKEN_DIR/.hosts.XXXXXX")
  printf 'github.com:\n    oauth_token: %s\n    user: x-access-token\n    git_protocol: https\n' \
    "$TOKEN" > "$tmp"
  mv -f "$tmp" "$TOKEN_DIR/hosts.yml"
  tmp=$(umask 077; mktemp "$TOKEN_DIR/.expires.XXXXXX")
  printf '%s\n' "$EXPIRES_AT" > "$tmp"
  mv -f "$tmp" "$TOKEN_DIR/expires_at"
}

# Environment for Claude sessions that act as the app. Used for
# .claude/settings.local.json (configure) and for agent-session.sh.
agent_env() {
  # shellcheck disable=SC2034  # read by the scripts that source this file
  declare -gA AGENT_ENV=(
    [GH_CONFIG_DIR]="$TOKEN_DIR"
    [GH_NO_UPDATE_NOTIFIER]="1"
    [GIT_AUTHOR_NAME]="$BOT_NAME"
    [GIT_AUTHOR_EMAIL]="$BOT_EMAIL"
    [GIT_COMMITTER_NAME]="$BOT_NAME"
    [GIT_COMMITTER_EMAIL]="$BOT_EMAIL"
    # git push over HTTPS gets the token from gh. The empty helper first
    # clears any other helper, so your own credentials are never used.
    [GIT_CONFIG_COUNT]="2"
    [GIT_CONFIG_KEY_0]="credential.https://github.com.helper"
    [GIT_CONFIG_VALUE_0]=""
    [GIT_CONFIG_KEY_1]="credential.https://github.com.helper"
    [GIT_CONFIG_VALUE_1]="!gh auth git-credential"
  )
}
