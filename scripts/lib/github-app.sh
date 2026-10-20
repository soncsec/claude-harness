# shellcheck shell=bash
#
# Shared GitHub App helpers for agent-token.sh and agent-session.sh.
# Source this file; it defines functions and paths, and runs nothing.
#
# Files (override the folders with the environment variables in brackets):
#   ~/.config/claude-harness/agent-app.conf   app settings      [CLAUDE_HARNESS_CONFIG_DIR]
#   ~/.config/claude-harness/agent-app.pem    app private key   (agents cannot read this folder)
#   ~/.config/claude-harness/repos            projects to keep tokens for, one owner/name per line
#   ~/.cache/claude-harness/<owner>/<repo>/   that repo's current token, in gh's format
#                                             [CLAUDE_HARNESS_TOKEN_DIR replaces ~/.cache/claude-harness]
#
# Every repo gets its own token, limited to that one repo.

HARNESS_CONFIG_DIR=${CLAUDE_HARNESS_CONFIG_DIR:-$HOME/.config/claude-harness}
CONFIG_FILE="$HARNESS_CONFIG_DIR/agent-app.conf"
KEY_FILE="$HARNESS_CONFIG_DIR/agent-app.pem"
REPOS_FILE="$HARNESS_CONFIG_DIR/repos"
TOKEN_ROOT=${CLAUDE_HARNESS_TOKEN_DIR:-${XDG_CACHE_HOME:-$HOME/.cache}/claude-harness}
# shellcheck disable=SC2034  # used by agent-token.sh
LEGACY_TOKEN_DIR="$TOKEN_ROOT/gh"  # single-repo layout used before per-project tokens
GITHUB_API=${GITHUB_API_URL:-https://api.github.com}

CLIENT_ID='' APP_SLUG='' LEGACY_REPO=''
TOKEN='' EXPIRES_AT='' BOT_NAME='' BOT_EMAIL=''

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
fail() { printf 'error: %s\n' "$*" >&2; return 1; }
log() { printf '==> %s\n' "$*"; }

require_tools() {
  local tool
  for tool in openssl curl python3; do
    command -v "$tool" >/dev/null || die "$tool is required"
  done
}

valid_repo() { [[ $1 =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; }

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
      APP_SLUG)        APP_SLUG=$value ;;
      REPO)            LEGACY_REPO=$value ;;  # older single-repo setups: still kept fresh
      INSTALLATION_ID) ;;                     # no longer needed: looked up per repo
      *)               die "unknown setting in $CONFIG_FILE: $key" ;;
    esac
  done < "$CONFIG_FILE"

  # None of these values are secret, so errors show what was found.
  [[ $CLIENT_ID =~ ^[A-Za-z0-9.]+$ ]] \
    || die "CLIENT_ID '$CLIENT_ID' is not valid: copy the Client ID (starts with Iv) from the app's settings page"
  [[ $APP_SLUG =~ ^[a-z0-9-]+$ ]] \
    || die "APP_SLUG '$APP_SLUG' must be the app's lowercase name, as in its URL"
  [[ -z $LEGACY_REPO ]] || valid_repo "$LEGACY_REPO" \
    || die "REPO '$LEGACY_REPO' must look like owner/name"
  BOT_NAME="${APP_SLUG}[bot]"
}

# --- Projects ------------------------------------------------------------------

token_dir_for() { printf '%s/%s' "$TOKEN_ROOT" "$1"; }

# Registered repos, one per line, sorted and without duplicates.
registered_repos() {
  {
    if [[ -f $REPOS_FILE ]]; then
      local line
      while IFS= read -r line || [[ -n $line ]]; do
        line=${line%$'\r'}
        line=${line%%#*}
        line=${line//[[:space:]]/}
        [[ -n $line ]] && valid_repo "$line" && printf '%s\n' "$line"
      done < "$REPOS_FILE"
    fi
    [[ -n $LEGACY_REPO ]] && printf '%s\n' "$LEGACY_REPO"
  } | sort -u
}

register_repo() {
  local repo=$1
  [[ $'\n'$(registered_repos)$'\n' == *$'\n'"$repo"$'\n'* ]] && return 0
  ( umask 077; printf '%s\n' "$repo" >> "$REPOS_FILE" )
}

unregister_repo() {
  local repo=$1 tmp
  [[ -f $REPOS_FILE ]] || return 0
  tmp=$(umask 077; mktemp "$REPOS_FILE.XXXXXX")
  grep -vxF "$repo" "$REPOS_FILE" > "$tmp" || true
  mv -f "$tmp" "$REPOS_FILE"
}

# owner/name of a project's GitHub remote (origin), or nothing.
repo_from_remote() {
  local url
  url=$(git -C "$1" remote get-url origin 2>/dev/null) || return 0
  url=${url%/}
  url=${url%.git}
  if [[ $url =~ ^(https://([^@/]*@)?|ssh://git@|git@)github\.com[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)$ ]]; then
    printf '%s/%s\n' "${BASH_REMATCH[3]}" "${BASH_REMATCH[4]}"
  fi
}

# --- GitHub App authentication ------------------------------------------------

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

JWT=''
app_jwt() {
  [[ -n $JWT ]] && return 0
  JWT=$(make_jwt) || die "could not sign with $KEY_FILE; is it the app's private key?"
}

# Mint a token for one repo, narrowed to that repo and these permissions even if
# the app was given more. Sets TOKEN and EXPIRES_AT; returns 1 on failure.
mint_token() {
  local repo=$1 response status installation body
  app_jwt
  response=$(github_api GET "/repos/$repo/installation" "$JWT") \
    || fail "could not reach GitHub (curl failed; see the message above)" || return 1
  status=${response##*$'\n'}
  if [[ $status == 404 ]]; then
    fail "the app is not installed on $repo: add it at https://github.com/settings/installations"
    return 1
  fi
  [[ $status == 200 ]] || { fail "GitHub refused the installation lookup for $repo (HTTP $status): ${response%$'\n'*}"; return 1; }
  installation=$(printf '%s' "${response%$'\n'*}" | json_field id)

  body=$(printf '{"repositories":["%s"],"permissions":{"metadata":"read","contents":"write","pull_requests":"write","issues":"write","checks":"read","statuses":"read","actions":"read"}}' "${repo#*/}")
  response=$(github_api POST "/app/installations/$installation/access_tokens" "$JWT" "$body") \
    || fail "could not reach GitHub to mint a token (curl failed; see the message above)" || return 1
  status=${response##*$'\n'}
  response=${response%$'\n'*}
  [[ $status == 201 ]] || { fail "GitHub refused to mint a token for $repo (HTTP $status): $response"; return 1; }
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

# --- Token files -----------------------------------------------------------------

# The token currently on disk in a folder, if any (to revoke it once replaced).
current_token() {
  [[ -f $1/hosts.yml ]] || return 0
  sed -n 's/^ *oauth_token: *//p' "$1/hosts.yml" | head -n1
}

# Write TOKEN where gh (and git, through gh) read it on every command, so a
# running Claude session picks up each renewal without restarting.
write_token_files() {
  local dir=$1 tmp
  ( umask 077; mkdir -p "$dir" )
  chmod 700 "$TOKEN_ROOT" "$dir" 2>/dev/null || true
  tmp=$(umask 077; mktemp "$dir/.hosts.XXXXXX")
  printf 'github.com:\n    oauth_token: %s\n    user: x-access-token\n    git_protocol: https\n' \
    "$TOKEN" > "$tmp"
  mv -f "$tmp" "$dir/hosts.yml"
  tmp=$(umask 077; mktemp "$dir/.expires.XXXXXX")
  printf '%s\n' "$EXPIRES_AT" > "$tmp"
  mv -f "$tmp" "$dir/expires_at"
}

# Mint a fresh token for one repo, store it, and revoke the one it replaces.
refresh_repo() {
  local repo=$1 dir previous
  dir=$(token_dir_for "$repo")
  previous=$(current_token "$dir")
  mint_token "$repo" || return 1
  write_token_files "$dir"
  [[ -n $previous && $previous != "$TOKEN" ]] && revoke_token "$previous"
  log "$repo: new token for $BOT_NAME, valid until $EXPIRES_AT."
}

# Environment for Claude sessions in a project that act as the app. Used for
# .claude/settings.local.json (configure) and for agent-session.sh.
agent_env() {
  local repo=$1
  # shellcheck disable=SC2034  # read by the scripts that source this file
  declare -gA AGENT_ENV=(
    [GH_CONFIG_DIR]="$(token_dir_for "$repo")"
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
