#!/usr/bin/env bash
#
# setup-debian.sh - install the tools claude-harness needs on Debian 13 (trixie).
#
# Safe to re-run: anything already installed is skipped.
# Run it as your normal user; only apt and group changes use sudo.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/setup-debian.sh [--check | --dry-run] [--yes] [--with-docker] [--force]

  --check        Report what is installed and exit. Changes nothing.
                 Exits 1 if a required tool is missing.
  --dry-run      Print the commands an install would run. Changes nothing.
  --yes          Do not ask before installing (for automation).
                 Never adds you to the docker group; that always needs a yes.
  --with-docker  Also install Docker Engine and Compose (needed for the
                 dev container and integration tests).
  --force        Run on a system that is not Debian 13 (unsupported).
  -h, --help     Show this help.

Where each tool comes from:
  Debian's signed archive  git, curl, make, python3, bubblewrap, socat, pipx, gh
                           with --with-docker: docker.io, docker-compose, docker-buildx
  PyPI, through pipx       uv (not packaged in Debian 13)

The script never adds third-party apt repositories, never pipes a download
into a shell, and never changes your groups without asking.
EOF
}

# ---------------------------------------------------------------------------
# Options and output helpers
# ---------------------------------------------------------------------------

USER=${USER:-$(id -un)}
MODE=install        # install | check | dry-run
ASSUME_YES=0
WITH_DOCKER=0
FORCE=0

while (( $# )); do
  case $1 in
    --check)       MODE=check ;;
    --dry-run)     MODE=dry-run ;;
    -y|--yes)      ASSUME_YES=1 ;;
    --with-docker) WITH_DOCKER=1 ;;
    --force)       FORCE=1 ;;
    -h|--help)     usage; exit 0 ;;
    *)             usage >&2; printf '\nerror: unknown option: %s\n' "$1" >&2; exit 2 ;;
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

# Run a command, or only print it in --dry-run mode.
run() {
  if [[ $MODE == dry-run ]]; then
    printf '  + %s\n' "$*"
  else
    "$@"
  fi
}

# Ask a yes/no question; default is no. --yes and --dry-run answer yes.
confirm() {
  local reply=''
  (( ASSUME_YES )) && return 0
  [[ $MODE == dry-run ]] && return 0
  read -r -p "$1 [y/N] " reply
  [[ $reply =~ ^[Yy]([Ee][Ss])?$ ]]
}

# Like confirm, but --yes does not count: only a person typing yes does.
confirm_strict() {
  local reply=''
  (( ASSUME_YES )) && return 1
  [[ $MODE == dry-run ]] && return 0
  [[ -t 0 ]] || return 1
  read -r -p "$1 [y/N] " reply
  [[ $reply =~ ^[Yy]([Ee][Ss])?$ ]]
}

# ---------------------------------------------------------------------------
# Tool catalogue
# ---------------------------------------------------------------------------

REQUIRED=(git curl make python3 bwrap socat pipx uv gh)
OPTIONAL=(docker compose claude)

declare -A APT_PKG=(
  [git]=git [curl]=curl [make]=make [python3]=python3
  [bwrap]=bubblewrap [socat]=socat [pipx]=pipx [gh]=gh
  [docker]=docker.io [compose]=docker-compose
)

declare -A PURPOSE=(
  [git]="version control"
  [curl]="downloads in scripts"
  [make]="the shared make commands"
  [python3]="hooks and system Python"
  [bwrap]="Claude Code sandbox (filesystem isolation)"
  [socat]="Claude Code sandbox (network proxy)"
  [pipx]="installs uv"
  [uv]="Python versions and locked dependencies"
  [gh]="GitHub from the terminal"
  [docker]="dev container and integration tests"
  [compose]="multi-container tests"
  [claude]="Claude Code itself"
)

has_tool() {
  case $1 in
    compose) command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 ;;
    *)       command -v "$1" >/dev/null 2>&1 ;;
  esac
}

# Print the first N words of the first line a command prints.
# (Avoids "cmd | head" pipes, which can fail under pipefail.)
first_words() {
  local n=$1 out
  local -a words
  shift
  out=$("$@" 2>&1) || true
  out=${out%%$'\n'*}
  read -r -a words <<< "$out"
  printf '%s\n' "${words[*]:0:n}"
}

version_of() {
  local line
  local -a words
  case $1 in
    git)     first_words 3 git --version ;;
    curl)    first_words 2 curl --version ;;
    make)    first_words 3 make --version ;;
    python3) first_words 2 python3 --version ;;
    bwrap)   first_words 2 bwrap --version ;;
    socat)
      while IFS= read -r line; do
        if [[ $line == *"socat version"* ]]; then
          read -r -a words <<< "$line"
          printf '%s\n' "${words[*]:0:3}"
          return
        fi
      done <<< "$(socat -V 2>/dev/null)"
      ;;
    pipx)    printf 'pipx %s\n' "$(first_words 1 pipx --version)" ;;
    uv)      first_words 2 uv --version ;;
    gh)      first_words 3 gh --version ;;
    docker)  first_words 3 docker --version ;;
    compose) first_words 4 docker compose version ;;
    claude)  first_words 3 claude --version ;;
  esac
}

# Print one row per tool. Returns 1 if a required tool is missing.
report() {
  local tool need status detail missing=0
  printf '\n%s%-8s %-9s %-8s %s%s\n' "$BOLD" "TOOL" "NEEDED" "STATUS" "VERSION / PURPOSE" "$RESET"
  for tool in "${REQUIRED[@]}" "${OPTIONAL[@]}"; do
    need=optional
    [[ " ${REQUIRED[*]} " == *" $tool "* ]] && need=required
    if has_tool "$tool"; then
      status="${GREEN}ok${RESET}      "
      detail=$(version_of "$tool" 2>/dev/null) || detail="installed"
    else
      status="${RED}missing${RESET} "
      detail=${PURPOSE[$tool]}
      [[ $need == required ]] && missing=1
    fi
    printf '%-8s %-9s %s %s\n' "$tool" "$need" "$status" "$detail"
  done
  return "$missing"
}

# Check that bubblewrap can create the namespaces Claude Code's sandbox needs.
sandbox_check() {
  if ! has_tool bwrap; then
    warn "sandbox check skipped: bubblewrap is not installed"
    return 1
  fi
  if bwrap --ro-bind / / --dev /dev --proc /proc --unshare-all --die-with-parent true 2>/dev/null; then
    log "Sandbox check passed: bubblewrap can create isolated namespaces."
  else
    warn "bubblewrap is installed but cannot create namespaces."
    warn "Check that unprivileged user namespaces are enabled (sysctl user.max_user_namespaces > 0)."
    return 1
  fi
}

# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

preflight() {
  local os_id os_version os_name
  [[ $EUID -ne 0 ]] || die "run this as your normal user, not root; it calls sudo only where needed"
  [[ -r /etc/os-release ]] || die "cannot read /etc/os-release"
  {
    IFS= read -r os_id
    IFS= read -r os_version
    IFS= read -r os_name
  } < <(
    # Read in a subshell so os-release variables do not leak into this script.
    # shellcheck source=/dev/null
    . /etc/os-release
    printf '%s\n%s\n%s\n' "${ID:-}" "${VERSION_ID:-}" "${PRETTY_NAME:-unknown}"
  )

  if [[ $os_id != debian || $os_version != 13 ]]; then
    (( FORCE )) || die "this script supports Debian 13 only (found: $os_name). Use --force to try anyway."
    warn "unsupported system ($os_name); continuing because of --force"
  else
    log "Detected $os_name."
  fi

  if [[ $MODE == install ]] && ! command -v sudo >/dev/null 2>&1; then
    die "sudo is required. As root: apt install sudo && usermod -aG sudo $USER, then log in again."
  fi
}

install_apt_packages() {
  local tool pkgs=()
  for tool in "${REQUIRED[@]}"; do
    [[ $tool == uv ]] && continue          # not in Debian 13; installed with pipx below
    has_tool "$tool" || pkgs+=("${APT_PKG[$tool]}")
  done
  if (( WITH_DOCKER )); then
    has_tool docker  || pkgs+=(docker.io docker-buildx)
    has_tool compose || pkgs+=(docker-compose)
  fi

  if (( ${#pkgs[@]} == 0 )); then
    log "All apt packages are already installed."
    return
  fi

  log "Packages to install from Debian's archive: ${pkgs[*]}"
  if ! confirm "Install them with apt (uses sudo)?"; then
    warn "Skipped apt packages."
    return
  fi
  run sudo apt-get update
  run sudo apt-get install -y "${pkgs[@]}"
}

install_uv() {
  if has_tool uv; then
    log "uv is already installed."
    return
  fi
  if ! has_tool pipx && [[ $MODE != dry-run ]]; then
    warn "pipx is missing, so uv cannot be installed. Install pipx first."
    return
  fi

  log "uv is not packaged in Debian 13; it will be installed from PyPI with pipx."
  if ! confirm "Install uv with pipx (into ~/.local/bin)?"; then
    warn "Skipped uv."
    return
  fi
  run pipx install uv

  # Make ~/.local/bin visible for the rest of this run and in future shells.
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *)
      export PATH="$HOME/.local/bin:$PATH"
      log "$HOME/.local/bin is not on your PATH; pipx can add it to your shell profile."
      if confirm "Run 'pipx ensurepath' to update your shell profile?"; then
        run pipx ensurepath
      fi
      ;;
  esac
}

configure_docker_group() {
  (( WITH_DOCKER )) || return 0
  if [[ " $(id -nG "$USER") " == *" docker "* ]]; then
    log "You are already in the docker group."
    return
  fi

  warn "Members of the docker group can get root access to this machine,"
  warn "and so can any agent running as you. Without the group, use 'sudo docker'."
  if confirm_strict "Add $USER to the docker group anyway?"; then
    run sudo usermod -aG docker "$USER"
    [[ $MODE == dry-run ]] || log "Added. Log out and back in for the group to take effect."
  else
    log "Not added. You can do it later with: sudo usermod -aG docker $USER"
  fi
}

next_steps() {
  printf '\n%sNext steps%s\n' "$BOLD" "$RESET"
  if ! has_tool claude; then
    printf '  - Install Claude Code: https://code.claude.com/docs\n'
  fi
  if has_tool gh && ! gh auth status >/dev/null 2>&1; then
    printf '  - Log in to GitHub: gh auth login\n'
  fi
  printf '  - Open a new terminal if PATH or group membership changed.\n'
  printf '  - Re-run with --check at any time to see what is installed.\n'
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

main() {
  preflight

  case $MODE in
    check)
      local status=0
      report || status=1
      echo
      sandbox_check || status=1
      exit "$status"
      ;;
    dry-run)
      log "Dry run: commands are printed, nothing is changed."
      ;;
  esac

  install_apt_packages
  install_uv
  configure_docker_group

  if [[ $MODE == dry-run ]]; then
    log "Dry run finished."
    exit 0
  fi

  local status=0
  report || status=1
  echo
  sandbox_check || status=1
  next_steps
  if (( status )); then
    warn "Some required tools are still missing or not working; see above."
  fi
  exit "$status"
}

main
