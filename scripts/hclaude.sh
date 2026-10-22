#!/usr/bin/env bash
#
# hclaude.sh - start Claude Code in a harness project with its GitHub API token
# masked. `agent-token.sh configure` links it as ~/.local/bin/hclaude.
#
# Inside the session's sandbox, gh sees a stand-in for the API token, and Claude
# Code's proxy swaps the real token in only on requests to api.github.com, so the
# agent can open pull requests and read CI but can never read or leak the token.
# Claude Code honors masking only from user, managed or --settings settings,
# which is why this passes the project's settings with --settings.
#
# Usage: hclaude [CLAUDE_ARGS...]   (run it inside the project, like claude)

set -euo pipefail

HARNESS_DIR=$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)
# shellcheck source=scripts/lib/github-app.sh
source "$HARNESS_DIR/scripts/lib/github-app.sh"

case ${1:-} in
  -h|--help)
    sed -n '3,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 0 ;;
esac

command -v claude >/dev/null || die "claude (Claude Code) is not on PATH"

top=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
repo=$(repo_from_remote "$top")
settings=''
[[ -n $repo ]] && settings=$(session_settings_for "$repo")

if [[ -n $settings && -f $settings ]]; then
  export CLAUDE_HARNESS_MASKED=1
  exec claude --settings "$settings" "$@"
fi

printf 'hclaude: %s has no masking settings (run agent-token.sh configure on it);\n' "${repo:-this folder}" >&2
printf 'hclaude: starting plain claude, which can read its GitHub token.\n' >&2
exec claude "$@"
