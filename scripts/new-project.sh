#!/usr/bin/env bash
#
# new-project.sh - set up a project with claude-harness, end to end, in one go.
#
# Run it yourself, outside Claude: it uses your own gh login (to create the repo
# and apply rules) and the agent app's key (to wire the agent's identity).

set -euo pipefail

HARNESS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/github-app.sh
source "$HARNESS_DIR/scripts/lib/github-app.sh"

usage() {
  cat <<'EOF'
Usage: scripts/new-project.sh [options] [DIR]

Sets up the project in DIR (default: the current directory):
  1. guardrails: settings, sandbox, hooks, CLAUDE.md (install-harness.sh)
  2. CODEOWNERS: changes to guardrail files need your review
  3. first commit: pushed straight to main for a new, empty repo;
     a pull request for a repo that already has history
  4. GitHub rules (github-setup.sh)
  5. the agent's own GitHub identity: opens the app's settings so you can tick
     the repo, waits until you have, then wires the token (agent-token.sh)

Options:
  --create OWNER/NAME  Create the GitHub repo first (private), then clone it into
                       DIR (default DIR: ./NAME).
  --public             With --create: make the repo public instead.
  --no-agent           Skip step 5 (the agent identity).
  --yes                Don't ask questions; use defaults (for automation and tests).
  --dry-run            Show the plan. Changes nothing.
  -h, --help           Show this help.

Examples:
  scripts/new-project.sh --create soncsec/my-app ~/dev/my-app
  scripts/new-project.sh ~/dev/existing-project
EOF
}

CREATE='' VISIBILITY=--private NO_AGENT=0 DRY_RUN=0 DIR='' YES=()
while (( $# )); do
  case $1 in
    --create)   shift; CREATE=${1:-} ;;
    --public)   VISIBILITY=--public ;;
    --no-agent) NO_AGENT=1 ;;
    --yes)      YES=(--yes) ;;
    --dry-run)  DRY_RUN=1 ;;
    -h|--help)  usage; exit 0 ;;
    -*)         usage >&2; printf '\nerror: unknown option: %s\n' "$1" >&2; exit 2 ;;
    *)          [[ -z $DIR ]] || { usage >&2; exit 2; }; DIR=$1 ;;
  esac
  shift
done

if [[ -t 1 ]]; then BOLD=$'\e[1m' RESET=$'\e[0m'; else BOLD='' RESET=''; fi
STEP=0
step() { STEP=$((STEP + 1)); printf '\n%s[%d] %s%s\n' "$BOLD" "$STEP" "$*" "$RESET"; }

# Run a command, or only print it in --dry-run mode.
run() {
  if (( DRY_RUN )); then printf '  + %s\n' "$*"; else "$@"; fi
}

SETUP_FILES=(.claude CLAUDE.md .gitignore .github/CODEOWNERS)

# OWNER's GitHub plan ("free", "pro", ...) when OWNER is your own account and
# GitHub shows the plan; empty otherwise (gh logins often lack the scope to read
# it, and an organization's plan is not checked here).
account_plan() {
  local line
  line=$(gh api user --jq '.login + " " + (.plan.name // "")' 2>/dev/null) || return 0
  [[ ${line%% *} == "$1" ]] && printf '%s' "${line#* }"
  return 0
}

# --- Agent identity helpers --------------------------------------------------------

# The URL where the owner picks which repos the app may use.
installation_page() {
  local owner=$1 response
  response=$(github_api GET /app/installations "$JWT") || return 1
  printf '%s' "${response%$'\n'*}" | python3 -c '
import json, sys
owner = sys.argv[1].lower()
for inst in json.load(sys.stdin):
    account = inst.get("account") or {}
    login = account.get("login") or ""
    if login.lower() == owner:
        if account.get("type") == "Organization":
            base = "https://github.com/organizations/" + login + "/settings/installations/"
        else:
            base = "https://github.com/settings/installations/"
        print(base + str(inst["id"]))
        break
' "$owner"
}

app_can_reach() {
  local response
  response=$(github_api GET "/repos/$1/installation" "$JWT") || return 1
  [[ ${response##*$'\n'} == 200 ]]
}

wire_agent_identity() {
  local repo=$1 page waited=0
  app_jwt
  if app_can_reach "$repo"; then
    log "The agent app can already reach $repo."
  else
    page=$(installation_page "${repo%%/*}") || true
    [[ -n $page ]] || die "the agent app is not installed on ${repo%%/*}'s account; see docs/setup/github-app.md"
    log "Add $repo to the agent app: open this page, tick it under 'Only select repositories', Save:"
    printf '\n    %s\n\n' "$page"
    if command -v xdg-open >/dev/null && [[ -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]]; then
      xdg-open "$page" >/dev/null 2>&1 || true
    fi
    log "Waiting for it (checking every 3 seconds; Ctrl-C to stop)..."
    until app_can_reach "$repo"; do
      sleep 3
      waited=$((waited + 3))
      (( waited < 600 )) || die "gave up after 10 minutes; re-run with: agent-token.sh configure $DIR"
      (( waited % 120 == 0 )) && JWT='' && app_jwt  # a JWT lasts 10 minutes
    done
    log "Got it: the agent app can reach $repo."
  fi
  "$HARNESS_DIR/scripts/agent-token.sh" configure "$DIR"
}

# --- Steps ---------------------------------------------------------------------

main() {
  local started=$SECONDS repo empty owner default_branch
  command -v git >/dev/null || die "git is required"
  command -v gh >/dev/null || die "gh (GitHub CLI) is required"
  command -v python3 >/dev/null || die "python3 is required"
  gh auth status >/dev/null 2>&1 || die "gh is not logged in; run: gh auth login"

  if (( ! NO_AGENT )) && [[ ! -f $CONFIG_FILE ]]; then
    log "No agent app configured ($CONFIG_FILE); skipping the agent identity step."
    log "To add it later: docs/setup/github-app.md, then agent-token.sh configure <dir>."
    NO_AGENT=1
  fi

  if [[ -n $CREATE ]]; then
    valid_repo "$CREATE" || die "--create needs OWNER/NAME"
    DIR=${DIR:-./${CREATE#*/}}
    [[ ! -e $DIR ]] || die "$DIR already exists"
    if [[ $VISIBILITY == --private ]]; then
      case $(account_plan "${CREATE%%/*}") in
        free)
          log "Note: on GitHub Free, private repos cannot have GitHub rules (rulesets)."
          log "      $CREATE will be protected by the local guardrails only."
          log "      For GitHub-side protection too, use --public or GitHub Pro." ;;
        '')
          log "Note: if ${CREATE%%/*} is on GitHub Free, a private repo cannot have GitHub"
          log "      rules (rulesets) and will be protected by the local guardrails only."
          log "      Step 5 will tell you which applies." ;;
      esac
    fi
    step "Create $CREATE on GitHub (${VISIBILITY#--}) and clone it into $DIR"
    run gh repo create "$CREATE" "$VISIBILITY" --description "Built with claude-harness"
    run git clone -q "https://github.com/$CREATE.git" "$DIR"
    repo=$CREATE
    empty=1
  else
    DIR=${DIR:-.}
    [[ -d $DIR ]] || die "$DIR does not exist (use --create OWNER/NAME to make a new repo)"
    git -C "$DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "$DIR is not a git repository"
    repo=$(repo_from_remote "$DIR")
    [[ -n $repo ]] || die "$DIR has no GitHub 'origin' remote"
    if [[ -z $(git -C "$DIR" ls-remote --heads origin 2>/dev/null) ]]; then empty=1; else empty=0; fi
  fi
  if (( ! DRY_RUN )); then DIR=$(cd "$DIR" && pwd); fi
  owner=${repo%%/*}

  log "Project: $DIR"
  log "Repo:    $repo ($( (( empty )) && echo 'new and empty: first commit goes straight to main' || echo 'has history: changes go in a pull request'))"
  (( DRY_RUN )) && log "Dry run: nothing will be changed."

  step "Install the guardrails"
  if (( DRY_RUN )); then
    printf '  + %s\n' "$HARNESS_DIR/scripts/install-harness.sh $DIR"
  else
    "$HARNESS_DIR/scripts/install-harness.sh" "${YES[@]}" "$DIR"
  fi

  step "CODEOWNERS: changes to guardrail files need @$owner's review"
  if [[ -f $DIR/.github/CODEOWNERS ]]; then
    log "Already present; left unchanged."
  elif (( DRY_RUN )); then
    printf '  + write %s/.github/CODEOWNERS from core/github/CODEOWNERS.template\n' "$DIR"
  else
    mkdir -p "$DIR/.github"
    sed "s/{{OWNER}}/$owner/g" "$HARNESS_DIR/core/github/CODEOWNERS.template" \
      > "$DIR/.github/CODEOWNERS"
  fi

  if (( empty )); then
    step "First commit, pushed straight to main (no rules exist yet)"
    run git -C "$DIR" add -- "${SETUP_FILES[@]}"
    run git -C "$DIR" commit -q -m "Set up claude-harness guardrails"
    run git -C "$DIR" branch -M main
    run git -C "$DIR" push -q -u origin main
  else
    default_branch=$(gh repo view "$repo" --json defaultBranchRef --jq .defaultBranchRef.name)
    step "Setup changes on a branch, as a pull request into $default_branch"
    run git -C "$DIR" switch -q -c claude-harness-setup
    run git -C "$DIR" add -- "${SETUP_FILES[@]}"
    run git -C "$DIR" commit -q -m "Set up claude-harness guardrails"
    run git -C "$DIR" push -q -u origin claude-harness-setup
    if (( DRY_RUN )); then
      printf '  + gh pr create --fill --base %s\n' "$default_branch"
    else
      (cd "$DIR" && gh pr create --fill --base "$default_branch")
    fi
  fi

  step "GitHub rules: only pull requests reach main"
  local protection="GitHub rules + local guardrails" rules_status=0
  if (( DRY_RUN )); then
    printf '  + %s\n' "(cd $DIR && $HARNESS_DIR/scripts/github-setup.sh $repo)"
  else
    (cd "$DIR" && "$HARNESS_DIR/scripts/github-setup.sh" "$repo") || rules_status=$?
  fi
  case $rules_status in
    0) ;;
    3) protection="local guardrails only (GitHub Free: no rulesets on private repos)" ;;
    *) protection="local guardrails only (GitHub rules FAILED: re-run github-setup.sh $repo)"
       printf 'warning: applying the GitHub rules failed (see above); continuing.\n' >&2 ;;
  esac

  if (( ! NO_AGENT )); then
    step "The agent's own GitHub identity"
    if (( DRY_RUN )); then
      printf '  + wait until the agent app can reach %s, then agent-token.sh configure %s\n' "$repo" "$DIR"
    else
      require_tools
      load_config
      wire_agent_identity "$repo"
    fi
  fi

  local elapsed=$((SECONDS - started))
  printf '\n%sDone in %dm %02ds.%s\n' "$BOLD" $((elapsed / 60)) $((elapsed % 60)) "$RESET"
  printf 'Protection: %s\n' "$protection"
  printf 'Next:\n'
  (( empty )) || printf '  - Merge the setup pull request (tick the bypass box: you cannot approve your own PR).\n'
  printf '  - cd %s && claude\n' "$DIR"
  printf '  - Optional, once per harness version: %s/docs/red-team-checklist.md\n' "$HARNESS_DIR"
}

main
