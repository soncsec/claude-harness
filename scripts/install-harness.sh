#!/usr/bin/env bash
#
# install-harness.sh - install the claude-harness template into a project.
#
# Copies guardrail settings, hooks and a starter CLAUDE.md into the target
# project. Shows a diff before replacing anything and never overwrites an
# existing CLAUDE.md. Safe to re-run to update an install.

set -euo pipefail
shopt -u patsub_replacement 2>/dev/null || true   # keep "&" literal in ${var//x/y}

usage() {
  cat <<'EOF'
Usage: scripts/install-harness.sh [--dry-run] [--yes] [--force] TARGET_DIR

  TARGET_DIR   The project to install into (use . for the current directory).
  --dry-run    Show what would change, including diffs. Changes nothing.
  --yes        Install new files without asking; use defaults for questions.
               Files that already exist and differ are still left alone
               unless you also pass --force.
  --force      Replace files that differ from the template (after showing diffs).
  -h, --help   Show this help.

What gets installed:
  .claude/settings.json      permissions, sandbox and hook wiring
  .claude/hooks/*.py         the guardrail hooks
  .claude/harness-version    which harness version was installed
  CLAUDE.md                  starter agent instructions (only if missing)
  .gitignore                 a marked block of secret and local-state rules

After installing, review the changes with `git diff`, commit them, and follow
docs/customizing.md to tailor the rules to your project.
EOF
}

# ---------------------------------------------------------------------------
# Options and helpers
# ---------------------------------------------------------------------------

HARNESS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CORE="$HARNESS_DIR/core"

MODE=install
ASSUME_YES=0
FORCE=0
TARGET=""

while (( $# )); do
  case $1 in
    --dry-run) MODE=dry-run ;;
    -y|--yes)  ASSUME_YES=1 ;;
    --force)   FORCE=1 ;;
    -h|--help) usage; exit 0 ;;
    -*)        usage >&2; printf '\nerror: unknown option: %s\n' "$1" >&2; exit 2 ;;
    *)
      [[ -z $TARGET ]] || { usage >&2; printf '\nerror: only one TARGET_DIR\n' >&2; exit 2; }
      TARGET=$1
      ;;
  esac
  shift
done

if [[ -t 1 ]]; then
  BOLD=$'\e[1m' RED=$'\e[31m' YELLOW=$'\e[33m' GREEN=$'\e[32m' RESET=$'\e[0m'
else
  BOLD='' RED='' YELLOW='' GREEN='' RESET=''
fi

log()  { printf '%s==>%s %s\n' "$BOLD" "$RESET" "$*"; }
warn() { printf '%swarning:%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$RED" "$RESET" "$*" >&2; exit 1; }

# Ask a yes/no question; default no. --yes answers yes.
confirm() {
  local reply=''
  (( ASSUME_YES )) && return 0
  [[ -t 0 ]] || return 1
  read -r -p "$1 [y/N] " reply
  [[ $reply =~ ^[Yy]([Ee][Ss])?$ ]]
}

# Ask for a line of text with a default. --yes or no terminal uses the default.
ask() {
  local reply=''
  if (( ASSUME_YES )) || [[ ! -t 0 ]]; then
    printf '%s\n' "$2"
    return
  fi
  read -r -p "$1 [$2]: " reply
  printf '%s\n' "${reply:-$2}"
}

# ---------------------------------------------------------------------------
# Plan: which template file goes where, and what state it is in
# ---------------------------------------------------------------------------

SOURCES=()   # template files
DESTS=()     # paths relative to TARGET
STATES=()    # new | same | changed

plan_file() {
  local src=$1 rel=$2 dest="$TARGET/$2" state
  if [[ ! -e $dest ]]; then
    state=new
  elif cmp -s "$src" "$dest"; then
    state=same
  else
    state=changed
  fi
  SOURCES+=("$src")
  DESTS+=("$rel")
  STATES+=("$state")
}

build_plan() {
  local hook
  plan_file "$CORE/claude/settings.json" ".claude/settings.json"
  for hook in "$CORE"/claude/hooks/*.py; do
    plan_file "$hook" ".claude/hooks/$(basename "$hook")"
  done
}

print_plan() {
  local i color
  printf '\n%sInstall plan for %s%s\n' "$BOLD" "$TARGET" "$RESET"
  for i in "${!DESTS[@]}"; do
    case ${STATES[$i]} in
      new)     color=$GREEN ;;
      changed) color=$YELLOW ;;
      *)       color='' ;;
    esac
    printf '  %s%-8s%s %s\n' "$color" "${STATES[$i]}" "$RESET" "${DESTS[$i]}"
  done
  if [[ -e $TARGET/CLAUDE.md ]]; then
    printf '  %-8s %s (yours is kept; compare with core/CLAUDE.template.md)\n' "keep" "CLAUDE.md"
  else
    printf '  %s%-8s%s %s\n' "$GREEN" "new" "$RESET" "CLAUDE.md"
  fi
  if grep -qF '# >>> claude-harness >>>' "$TARGET/.gitignore" 2>/dev/null; then
    printf '  %-8s %s\n' "same" ".gitignore (harness block already present)"
  else
    printf '  %s%-8s%s %s\n' "$GREEN" "append" "$RESET" ".gitignore (harness block)"
  fi
  echo
}

# ---------------------------------------------------------------------------
# Install steps
# ---------------------------------------------------------------------------

install_files() {
  local i src rel dest replaced=0 skipped=0
  for i in "${!DESTS[@]}"; do
    src=${SOURCES[$i]} rel=${DESTS[$i]} dest="$TARGET/${DESTS[$i]}"
    case ${STATES[$i]} in
      same) continue ;;
      changed)
        printf '\n%sYour %s differs from the template:%s\n' "$BOLD" "$rel" "$RESET"
        diff -u "$dest" "$src" --label "yours: $rel" --label "template: $rel" || true
        if [[ $MODE == dry-run ]]; then
          continue
        fi
        if (( FORCE )) || { (( ! ASSUME_YES )) && confirm "Replace your $rel with the template?"; }; then
          install -D -m 0644 "$src" "$dest"
          replaced=$((replaced + 1))
        else
          warn "Kept your $rel. Merge the differences by hand if you want them."
          skipped=$((skipped + 1))
        fi
        ;;
      new)
        [[ $MODE == dry-run ]] || install -D -m 0644 "$src" "$dest"
        ;;
    esac
  done
  if (( skipped )); then
    warn "$skipped changed file(s) kept as they were."
  fi
  if (( replaced )); then
    log "Replaced $replaced file(s)."
  fi
}

install_claude_md() {
  local dest="$TARGET/CLAUDE.md" name description content
  [[ -e $dest ]] && return 0
  [[ $MODE == dry-run ]] && return 0
  name=$(ask "Project name" "$(basename "$(cd "$TARGET" && pwd)")")
  description=$(ask "One line about what it does" "TODO: describe this project in one line.")
  content=$(<"$CORE/CLAUDE.template.md")
  content=${content//\{\{PROJECT_NAME\}\}/$name}
  content=${content//\{\{DESCRIPTION\}\}/$description}
  printf '%s\n' "$content" > "$dest"
}

install_gitignore() {
  local dest="$TARGET/.gitignore"
  grep -qF '# >>> claude-harness >>>' "$dest" 2>/dev/null && return 0
  [[ $MODE == dry-run ]] && return 0
  if [[ -s $dest && -n $(tail -c1 "$dest") ]]; then
    printf '\n' >> "$dest"   # make sure the block starts on its own line
  fi
  cat "$CORE/gitignore.snippet" >> "$dest"
}

record_version() {
  local version
  [[ $MODE == dry-run ]] && return 0
  version=$(git -C "$HARNESS_DIR" describe --always --dirty 2>/dev/null || echo unknown)
  printf 'claude-harness %s\ninstalled %s\n' "$version" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    > "$TARGET/.claude/harness-version"
}

# Feed one dangerous command to the installed Bash hook; it must say "deny".
smoke_test() {
  local out
  out=$(printf '%s' '{"tool_name":"Bash","tool_input":{"command":"sudo true"}}' \
        | CLAUDE_PROJECT_DIR="$TARGET" python3 "$TARGET/.claude/hooks/guard_bash.py" 2>&1) || true
  if [[ $out == *'"deny"'* ]]; then
    log "Smoke test passed: the Bash guard blocks a sudo command."
  else
    warn "Smoke test failed: the Bash guard did not block 'sudo true'. Output: $out"
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  [[ -n $TARGET ]] || { usage >&2; exit 2; }
  [[ -d $TARGET ]] || die "target directory does not exist: $TARGET"
  TARGET=$(cd "$TARGET" && pwd)
  command -v python3 >/dev/null || die "python3 is required for the hooks"
  [[ -f $CORE/claude/settings.json ]] || die "template not found in $CORE"

  if ! git -C "$TARGET" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    warn "$TARGET is not a git repository. Run 'git init' there so you can review and commit the install."
  fi

  build_plan
  print_plan

  if [[ $MODE == dry-run ]]; then
    install_files
    log "Dry run: nothing was changed."
    exit 0
  fi

  confirm "Install the harness into $TARGET?" || { log "Cancelled; nothing was changed."; exit 0; }

  install_files
  install_claude_md
  install_gitignore
  record_version

  local status=0
  smoke_test || status=1

  printf '\n%sNext steps%s\n' "$BOLD" "$RESET"
  printf '  1. Review the changes:   git -C %q diff; git -C %q status\n' "$TARGET" "$TARGET"
  printf '  2. Tailor the rules:     %s/docs/customizing.md\n' "$HARNESS_DIR"
  printf '  3. Commit the install, then start a new Claude Code session in the project.\n'
  printf '  4. Prove it works:       %s/docs/red-team-checklist.md\n' "$HARNESS_DIR"
  exit "$status"
}

main
