#!/usr/bin/env bash
#
# github-setup.sh - apply the harness's GitHub rules to a repository.
#
# Run it yourself, as the repository admin, with your own gh login. It sets the
# rules that keep merging and approving with you even when an agent can push.
# Safe to re-run: existing rulesets are found by name and updated.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/github-setup.sh [--dry-run] OWNER/REPO

  --dry-run   Show what would change. Changes nothing.
  -h, --help  Show this help.

What it applies:
  ruleset protect-main   Changes reach the default branch only through pull
                         requests; those touching guardrail files need your
                         Code Owner review. No force pushes, no deletion.
                         (core/github/ruleset-main.json)
  ruleset protect-tags   Only you create, move or delete tags.
                         (core/github/ruleset-tags.json)
  repo settings          Auto-merge off; merged branches deleted; the Actions
                         token is read-only and cannot approve pull requests.
  .github/CODEOWNERS     Created in the current git repo if missing, from
                         core/github/CODEOWNERS.template: you own the guardrail
                         files. Commit it yourself; agents cannot edit it.

Exit status 3 means rulesets are not available on this repo (GitHub Free only
has them on public repos); everything else was applied.
EOF
}

HARNESS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
RULESETS=("$HARNESS_DIR/core/github/ruleset-main.json" "$HARNESS_DIR/core/github/ruleset-tags.json")

DRY_RUN=0
REPO=''
while (( $# )); do
  case $1 in
    --dry-run) DRY_RUN=1 ;;
    -h|--help) usage; exit 0 ;;
    -*)        usage >&2; printf '\nerror: unknown option: %s\n' "$1" >&2; exit 2 ;;
    *)         REPO=$1 ;;
  esac
  shift
done

die() { printf 'error: %s\n' "$*" >&2; exit 1; }
log() { printf '==> %s\n' "$*"; }

# Run a command, or only print it in --dry-run mode.
run() {
  if (( DRY_RUN )); then
    printf '  + %s\n' "$*"
  else
    "$@"
  fi
}

json_name() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$1"; }

RULESETS_JSON=''

# Read the repo's rulesets. Returns 2 when GitHub's plan does not allow rulesets
# here (GitHub Free only has them on public repos); dies on any other error.
load_rulesets() {
  local out
  if out=$(gh api "repos/$REPO/rulesets" 2>&1); then
    RULESETS_JSON=$out
    return 0
  fi
  if [[ $out == *"Upgrade to GitHub Pro"* || $out == *"make this repository public"* ]]; then
    return 2
  fi
  die "could not read the rulesets of $REPO: $out"
}

ruleset_id() {
  printf '%s' "$RULESETS_JSON" | python3 -c '
import json, sys
for ruleset in json.load(sys.stdin):
    if ruleset.get("name") == sys.argv[1]:
        print(ruleset["id"])
        break
' "$1"
}

apply_ruleset() {
  local file=$1 name id
  name=$(json_name "$file")
  id=$(ruleset_id "$name")
  if [[ -n $id ]]; then
    log "Updating ruleset $name (id $id)"
    run gh api -X PUT "repos/$REPO/rulesets/$id" --input "$file" --silent
  else
    log "Creating ruleset $name"
    run gh api -X POST "repos/$REPO/rulesets" --input "$file" --silent
  fi
}

apply_settings() {
  log "Repository settings: auto-merge off, delete merged branches"
  run gh repo edit "$REPO" --enable-auto-merge=false --delete-branch-on-merge
  log "Actions: read-only token, cannot approve pull requests"
  run gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
    -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false --silent
}

write_codeowners() {
  local top owner file
  if ! top=$(git rev-parse --show-toplevel 2>/dev/null); then
    log "Not inside a git repo; skipping CODEOWNERS. Create .github/CODEOWNERS yourself."
    return
  fi
  owner=${REPO%%/*}
  file="$top/.github/CODEOWNERS"
  if [[ -f $file ]]; then
    log "CODEOWNERS already exists ($file); left unchanged."
    return
  fi
  log "Creating $file: changes to guardrail files need @$owner's review"
  if (( ! DRY_RUN )); then
    mkdir -p "$top/.github"
    sed "s/{{OWNER}}/$owner/g" "$HARNESS_DIR/core/github/CODEOWNERS.template" > "$file"
  fi
}

main() {
  [[ $REPO =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || { usage >&2; exit 2; }
  command -v gh >/dev/null || die "gh (GitHub CLI) is required"
  command -v python3 >/dev/null || die "python3 is required"
  gh auth status >/dev/null 2>&1 || die "gh is not logged in; run: gh auth login"
  gh api "repos/$REPO" --jq .permissions.admin 2>/dev/null | grep -qx true \
    || die "you need admin access to $REPO"

  (( DRY_RUN )) && log "Dry run: nothing will be changed."
  local file rules=1
  load_rulesets || rules=0
  if (( rules )); then
    for file in "${RULESETS[@]}"; do
      apply_ruleset "$file"
    done
  else
    warn_no_rulesets
  fi
  apply_settings
  write_codeowners

  if (( ! DRY_RUN )); then
    if (( rules )); then
      log "Done. Rulesets now on $REPO:"
      gh api "repos/$REPO/rulesets" --jq '.[] | "  - \(.name) (\(.enforcement))"'
    else
      log "Done, without GitHub rules: $REPO is protected by the local guardrails only."
    fi
    log "Next: commit .github/CODEOWNERS if it was created, then follow the tests in docs/setup/github-app.md."
  fi
  (( rules )) || exit 3
}

warn_no_rulesets() {
  cat >&2 <<EOF

warning: GitHub's plan does not allow rulesets on $REPO.
  GitHub Free has rulesets (branch protection) only on public repositories.
  Without them, GitHub itself does not stop pushes to main, force pushes, or
  merges without your review, and CODEOWNERS is advisory only. Agents are
  still stopped by the local guardrails (deny rules, Bash hook, sandbox).
  For GitHub-side protection too: make the repo public, or upgrade to
  GitHub Pro, then run this script again.

EOF
}

main
