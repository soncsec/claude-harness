#!/usr/bin/env bash
#
# update-guardrails.sh - put the latest harness guardrails into a project, in one go.
#
# Agents cannot install or change their own guardrails, so this is the
# operator's step. It replaces the usual routine: pull, install, branch,
# commit, push, open a pull request, wait for CI, merge, re-apply the rules.
# Run it yourself, outside Claude.

set -euo pipefail

HARNESS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/github-app.sh
source "$HARNESS_DIR/scripts/lib/github-app.sh"

usage() {
  cat <<'EOF'
Usage: scripts/update-guardrails.sh [--yes] [--no-merge] [--dry-run] [DIR]

Updates the project in DIR (default: the current directory) to this harness
version's guardrails: settings, hooks and CI workflows. It shows what would
change and asks once, then:

  1. installs the changes on a new branch and opens a pull request
  2. waits for the CI checks on it, and merges it with your admin rights
     once they pass (it stops if a check fails: nothing broken is merged)
  3. re-applies the GitHub rules (github-setup.sh)

If nothing changed, it only re-applies the rules.

  --yes       Don't ask; go ahead.
  --no-merge  Stop after opening the pull request.
  --dry-run   Show what would change. Changes nothing.
  -h, --help  Show this help.
EOF
}

ASSUME_YES=0 NO_MERGE=0 DRY_RUN=0 DIR=''
while (( $# )); do
  case $1 in
    --yes|-y)   ASSUME_YES=1 ;;
    --no-merge) NO_MERGE=1 ;;
    --dry-run)  DRY_RUN=1 ;;
    -h|--help)  usage; exit 0 ;;
    -*)         usage >&2; printf '\nerror: unknown option: %s\n' "$1" >&2; exit 2 ;;
    *)          [[ -z $DIR ]] || { usage >&2; exit 2; }; DIR=$1 ;;
  esac
  shift
done

if [[ -t 1 ]]; then BOLD=$'\e[1m' RESET=$'\e[0m'; else BOLD='' RESET=''; fi
step() { printf '\n%s%s%s\n' "$BOLD" "$*" "$RESET"; }

INSTALLED_PATHS=(.claude .github/workflows .gitignore)

confirm() {
  local reply=''
  (( ASSUME_YES )) && return 0
  [[ -t 0 ]] || return 1
  read -r -p "$1 [y/N] " reply
  [[ $reply =~ ^[Yy]([Ee][Ss])?$ ]]
}

# Wait until CI reports on the pull request, then until every check finishes.
# Returns 0 when all pass (or no CI is set up), 1 when a check fails.
CHECKS_TRIES=${UPDATE_GUARDRAILS_CHECK_TRIES:-12}   # x 10 seconds: wait up to 2 minutes
CHECKS_SLEEP=${UPDATE_GUARDRAILS_CHECK_SLEEP:-10}   # for CI to start reporting

wait_for_checks() {
  local pr=$1 tries=0 out
  while :; do
    out=$(cd "$DIR" && gh pr checks "$pr" 2>&1) && return 0  # all passed already
    case $out in
      *"no checks reported"*)
        if (( tries++ >= CHECKS_TRIES )); then
          log "No CI checks were reported; continuing without them."
          return 0
        fi
        sleep "$CHECKS_SLEEP" ;;
      *) break ;;
    esac
  done
  log "Waiting for the CI checks to finish (Ctrl-C to stop; the pull request stays open)..."
  (cd "$DIR" && gh pr checks "$pr" --watch --fail-fast --interval 15)
}

main() {
  local repo default_branch branch pr_url pr status=0
  command -v git >/dev/null || die "git is required"
  command -v gh >/dev/null || die "gh (GitHub CLI) is required"
  gh auth status >/dev/null 2>&1 || die "gh is not logged in; run: gh auth login"

  DIR=$(cd "${DIR:-.}" && pwd)
  repo=$(repo_from_remote "$DIR")
  [[ -n $repo ]] || die "$DIR has no GitHub 'origin' remote"
  [[ -z $(git -C "$DIR" status --porcelain --untracked-files=no) ]] \
    || die "$DIR has uncommitted changes; commit or stash them first"
  default_branch=$(gh repo view "$repo" --json defaultBranchRef --jq .defaultBranchRef.name)

  step "Bring $DIR up to date with $default_branch"
  git -C "$DIR" switch -q "$default_branch"
  git -C "$DIR" pull -q --ff-only

  step "What would change"
  "$HARNESS_DIR/scripts/install-harness.sh" --dry-run "$DIR"

  if (( DRY_RUN )); then
    log "Dry run: nothing was changed."
    return
  fi

  local untracked_before new_file
  untracked_before=$(git -C "$DIR" ls-files --others --exclude-standard -- "${INSTALLED_PATHS[@]}")
  "$HARNESS_DIR/scripts/install-harness.sh" --yes --force "$DIR" >/dev/null
  if [[ -z $(git -C "$DIR" status --porcelain -- "${INSTALLED_PATHS[@]}") ]]; then
    log "The guardrails are already up to date."
  else
    git -C "$DIR" diff --stat -- "${INSTALLED_PATHS[@]}"
    git -C "$DIR" ls-files --others --exclude-standard -- "${INSTALLED_PATHS[@]}" \
      | sed 's/^/ new file: /'
    if ! confirm "Install these changes, open a pull request$( (( NO_MERGE )) || printf ', wait for CI and merge it')?"; then
      # Put everything back: restore changed files, remove only the files just added.
      git -C "$DIR" checkout -q -- "${INSTALLED_PATHS[@]}" 2>/dev/null || true
      while IFS= read -r new_file; do
        [[ -n $new_file ]] || continue
        grep -qxF "$new_file" <<< "$untracked_before" || rm -f -- "$DIR/$new_file"
      done < <(git -C "$DIR" ls-files --others --exclude-standard -- "${INSTALLED_PATHS[@]}")
      log "Cancelled; the files are back as they were."
      return
    fi

    step "Pull request"
    branch="update-guardrails-$(date +%Y%m%d-%H%M%S)"
    git -C "$DIR" switch -q -c "$branch"
    git -C "$DIR" add -- "${INSTALLED_PATHS[@]}"
    git -C "$DIR" commit -q -m "Update claude-harness guardrails ($(head -n1 "$DIR/.claude/harness-version"))"
    git -C "$DIR" push -q -u origin "$branch"
    pr_url=$(cd "$DIR" && gh pr create --fill --base "$default_branch" --head "$branch")
    pr=${pr_url##*/}
    log "Opened $pr_url"

    if (( NO_MERGE )); then
      git -C "$DIR" switch -q "$default_branch"
      log "Stopped before merging (--no-merge). Merge it when the checks pass, then run:"
      log "  $HARNESS_DIR/scripts/github-setup.sh $repo"
      return
    fi

    step "CI"
    if ! wait_for_checks "$pr"; then
      git -C "$DIR" switch -q "$default_branch"
      die "a CI check failed on $pr_url; nothing was merged. Fix it, or ask Claude to, then merge."
    fi

    step "Merge"
    # Your own PR touching guardrail files needs your admin bypass to merge.
    (cd "$DIR" && gh pr merge "$pr" --merge --admin --delete-branch)
    git -C "$DIR" switch -q "$default_branch"
    git -C "$DIR" pull -q --ff-only
  fi

  step "GitHub rules"
  (cd "$DIR" && "$HARNESS_DIR/scripts/github-setup.sh" "$repo") || status=$?
  case $status in
    0|3) ;;
    *) die "re-applying the GitHub rules failed (see above); run github-setup.sh $repo again" ;;
  esac
  printf '\n%sDone: %s has the current guardrails.%s\n' "$BOLD" "$repo" "$RESET"
}

main
