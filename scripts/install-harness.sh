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
  .claude/rules/harness.md   the rules every agent follows (kept up to date)
  .claude/tools/watch_pr.py  lets agents wait on a pull request's checks and merge
  .claude/skills/kickoff/    the /kickoff skill: plan a project, then build its skeleton
  .claude/harness-version    which harness version was installed
  .github/workflows/*.yml    CI: checks and security scans on every pull request
  .github/harness/*.py       CI's test-integrity and coverage checkers
  .github/ISSUE_TEMPLATE/    issue forms (only those missing; yours are kept)
  quality-gates.toml         coverage minimums CI enforces (only if missing; an
                             empty coverage list gets the projects found)
  CLAUDE.md                  starter notes about the project (only if missing;
                             old harness sections in yours move to .claude/rules/)
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
CLAUDE_MD_TOOL="$HARNESS_DIR/scripts/lib/claude_md.py"

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

# Starter files belong to the project once installed: added when missing, never replaced.
KEPT=()
plan_starter() {
  if [[ -e $TARGET/$2 ]]; then
    KEPT+=("$2")
  else
    plan_file "$1" "$2"
  fi
}

build_plan() {
  local hook workflow checker form
  plan_file "$CORE/claude/settings.json" ".claude/settings.json"
  for hook in "$CORE"/claude/hooks/*.py; do
    plan_file "$hook" ".claude/hooks/$(basename "$hook")"
  done
  local rules
  for rules in "$CORE"/claude/rules/*.md; do
    plan_file "$rules" ".claude/rules/$(basename "$rules")"
  done
  local tool
  for tool in "$CORE"/claude/tools/*.py; do
    plan_file "$tool" ".claude/tools/$(basename "$tool")"
  done
  for workflow in "$CORE"/github/workflows/*.yml; do
    plan_file "$workflow" ".github/workflows/$(basename "$workflow")"
  done
  for checker in "$CORE"/github/harness/*.py; do
    plan_file "$checker" ".github/harness/$(basename "$checker")"
  done
  for form in "$CORE"/github/ISSUE_TEMPLATE/*.yml; do
    plan_starter "$form" ".github/ISSUE_TEMPLATE/$(basename "$form")"
  done
  local file
  while IFS= read -r file; do
    plan_file "$file" ".claude/skills/${file#"$CORE/claude/skills/"}"
  done < <(find "$CORE/claude/skills" -type f | sort)
}

# Coverage entries for every Python project found: a folder (up to two levels
# down) with both a uv.lock and a src/ folder.
detect_projects() {
  local lock dir rel
  while IFS= read -r lock; do
    dir=$(dirname "$lock")
    [[ -d $dir/src ]] || continue
    rel=${dir#"$TARGET"}
    rel=${rel#/}
    printf '  { path = "%s", source = ["src"], minimum = 80 },\n' "${rel:-.}"
  done < <(find "$TARGET" -maxdepth 3 -name uv.lock -not -path '*/.cache/*' -not -path '*/.venv/*' | sort)
}

# True when quality-gates.toml exists with an empty coverage list.
coverage_list_is_empty() {
  python3 -c 'import sys, tomllib
data = tomllib.load(open(sys.argv[1], "rb"))
sys.exit(0 if data.get("coverage", {}).get("projects") == [] else 1)' "$1" 2>/dev/null
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
  for i in "${!KEPT[@]}"; do
    printf '  %-8s %s (yours is kept)\n' "keep" "${KEPT[$i]}"
  done
  if [[ -e $TARGET/CLAUDE.md ]]; then
    local trim status=0
    trim=$(python3 "$CLAUDE_MD_TOOL" --check "$TARGET/CLAUDE.md") || status=$?
    case $status in
      0) printf '  %-8s %s (yours is kept)\n' "keep" "CLAUDE.md" ;;
      3) printf '  %s%-8s%s %s\n' "$YELLOW" "trim" "$RESET" \
           "CLAUDE.md (old harness sections move out; your own text is kept)" ;;
      *) die "could not read $TARGET/CLAUDE.md" ;;
    esac
    local line
    while IFS= read -r line; do
      [[ -z $line ]] || printf '             %s\n' "$line"
    done <<< "$trim"
  else
    printf '  %s%-8s%s %s\n' "$GREEN" "new" "$RESET" "CLAUDE.md"
  fi
  if [[ -e $TARGET/quality-gates.toml ]]; then
    if coverage_list_is_empty "$TARGET/quality-gates.toml" && [[ -n $(detect_projects) ]]; then
      printf '  %s%-8s%s %s\n' "$YELLOW" "fill" "$RESET" \
        "quality-gates.toml (its empty coverage list gets the projects found)"
    else
      printf '  %-8s %s\n' "keep" "quality-gates.toml (yours is kept)"
    fi
  else
    printf '  %s%-8s%s %s\n' "$GREEN" "new" "$RESET" "quality-gates.toml"
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

# Create quality-gates.toml if missing, measuring every Python project found.
# If it exists with an empty coverage list (the project had no code yet when the
# harness was installed), fill that list in. A list with entries is never changed.
install_quality_gates() {
  local dest="$TARGET/quality-gates.toml" projects content
  [[ $MODE == dry-run ]] && return 0
  projects=$(detect_projects)
  if [[ -e $dest ]]; then
    if [[ -z $projects ]] || ! coverage_list_is_empty "$dest"; then
      return 0
    fi
    if python3 -c 'import re, sys
path, projects = sys.argv[1], sys.argv[2]
text = open(path).read()
text, count = re.subn(r"^projects = \[\s*\]", lambda _: "projects = [\n" + projects + "\n]",
                      text, count=1, flags=re.MULTILINE)
if count:
    open(path, "w").write(text)' "$dest" "$projects"; then
      log "Coverage gate: added the projects found to quality-gates.toml."
    else
      warn "Could not update quality-gates.toml; add the coverage entries by hand."
    fi
    return 0
  fi
  [[ -n $projects ]] && projects+=$'\n'
  content=$(<"$CORE/quality-gates.template.toml")
  content=${content//\{\{PROJECTS\}\}/$projects}
  printf '%s\n' "$content" > "$dest"
}

install_claude_md() {
  local dest="$TARGET/CLAUDE.md" name description content
  [[ $MODE == dry-run ]] && return 0
  if [[ -e $dest ]]; then
    # The harness's rules now live in .claude/rules/harness.md: take the old copies out.
    python3 "$CLAUDE_MD_TOOL" "$dest" >/dev/null
    return 0
  fi
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
  local version=$HARNESS_VERSION file="$TARGET/.claude/harness-version"
  [[ $MODE == dry-run ]] && return 0
  # Same version already recorded: leave the file alone, so re-running the
  # installer does not show a change (the install time would differ).
  [[ -f $file && $(head -n1 "$file") == "claude-harness $version" ]] && return 0
  printf 'claude-harness %s\ninstalled %s\n' "$version" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$file"
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

  # Read the version before installing anything: when the harness installs into
  # itself, the freshly installed files would otherwise mark it "-dirty".
  HARNESS_VERSION=$(git -C "$HARNESS_DIR" describe --always --dirty 2>/dev/null || echo unknown)
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
  install_quality_gates
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
