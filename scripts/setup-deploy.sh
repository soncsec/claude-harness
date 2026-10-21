#!/usr/bin/env bash
#
# setup-deploy.sh - turn on deploys for a project: staging on every merge,
# production after the operator approves.
#
# Run it yourself, with your own gh login, from anywhere. It never deploys
# anything; it prepares GitHub and prints the command to run on each server.
# Re-running it makes new deploy keys, so run the printed server commands again.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/setup-deploy.sh [options] --staging USER@HOST --production USER@HOST PROJECT_DIR

  --staging USER@HOST     where staging deploys go (USER is normally deploy)
  --production USER@HOST  where production deploys go
  --app NAME              the app's name on the servers (default: the repo name)
  --port PORT             the port the app listens on (default 8000)
  --health-path PATH      answers 2xx once the app is up (default /health)
  --runner LABEL          run the deploy jobs on your own runner with this label,
                          for servers GitHub's runners cannot reach (a private
                          network); see scripts/setup-deploy-runner.sh
  --dry-run               show what would change; change nothing
  -h, --help              show this help

What it does:
  workflow       copies the harness's deploy workflow to
                 .github/workflows/deploy.yml (commit it through a pull request)
  environments   "staging", and "production" with you as its required reviewer;
                 both accept deploys from the default branch only
  deploy keys    a new SSH key per environment: the private half becomes the
                 environment's DEPLOY_SSH_KEY secret, the public half goes in
                 the server command it prints
  host keys      each server's SSH host keys become DEPLOY_KNOWN_HOSTS, so CI
                 refuses an impostor (compare the fingerprints it prints)
  DEPLOY_HOST    each environment's USER@HOST variable
  --runner       sets the DEPLOY_RUNNER variable, and on public repos makes every
                 outside contributor's workflow wait for your approval, so no
                 one else's code can run on your runner

Then run the printed setup-deploy-host.sh command on each server.
See docs/deploy.md.
EOF
}

HARNESS_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/lib/github-app.sh
source "$HARNESS_DIR/scripts/lib/github-app.sh"
WORKFLOW_SOURCE="$HARNESS_DIR/core/deploy/deploy.yml"

STAGING='' PRODUCTION='' APP='' PORT=8000 HEALTH_PATH=/health RUNNER='' DRY_RUN=0 DIR=''
while (( $# )); do
  case $1 in
    --staging)     STAGING=${2:-}; shift ;;
    --production)  PRODUCTION=${2:-}; shift ;;
    --app)         APP=${2:-}; shift ;;
    --port)        PORT=${2:-}; shift ;;
    --health-path) HEALTH_PATH=${2:-}; shift ;;
    --runner)      RUNNER=${2:-}; shift ;;
    --dry-run)     DRY_RUN=1 ;;
    -h|--help)     usage; exit 0 ;;
    -*)            usage >&2; die "unknown option: $1" ;;
    *)             DIR=$1 ;;
  esac
  shift
done

run() {
  if (( DRY_RUN )); then printf '  + %s\n' "$*"; else "$@"; fi
}

valid_target() { [[ $1 =~ ^[a-z_][a-z0-9_-]*@[A-Za-z0-9.-]+$ ]]; }

[[ -n $DIR && -d $DIR ]] || { usage >&2; die "give the project folder"; }
valid_target "$STAGING" || die "--staging must be USER@HOST, e.g. deploy@staging.example.com"
valid_target "$PRODUCTION" || die "--production must be USER@HOST, e.g. deploy@app.example.com"
if [[ ! $PORT =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then die "--port must be 1-65535"; fi
[[ $HEALTH_PATH =~ ^/[A-Za-z0-9._~/-]*$ ]] || die "--health-path must start with / and have no spaces"
if [[ -n $RUNNER && ! $RUNNER =~ ^[a-z0-9][a-z0-9-]{0,40}$ ]]; then
  die "--runner must be lowercase letters, digits and dashes"
fi
for tool in gh git ssh-keygen ssh-keyscan; do
  command -v "$tool" >/dev/null || die "$tool is required"
done

DIR=$(cd "$DIR" && pwd)
REPO=$(repo_from_remote "$DIR")
[[ -n $REPO ]] || die "$DIR has no GitHub 'origin' remote"
IMAGE="ghcr.io/${REPO,,}"
if [[ -z $APP ]]; then
  APP=$(printf '%s' "${REPO#*/}" | tr '[:upper:]_.' '[:lower:]--')
fi
[[ $APP =~ ^[a-z0-9][a-z0-9-]{0,62}$ ]] || die "--app must be lowercase letters, digits and dashes"

DEFAULT_BRANCH=$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)
# The workflow deploys from main, and harness updates keep it identical to the template.
[[ $DEFAULT_BRANCH == main ]] || die "deploys need the default branch to be 'main' ($REPO uses '$DEFAULT_BRANCH')"
VISIBILITY=$(gh repo view "$REPO" --json visibility --jq .visibility)
IFS=$'\t' read -r ME_ID ME_LOGIN ME_PLAN < <(gh api user --jq '[.id, .login, (.plan.name // "unknown")] | @tsv')

# Environments (the deploy secrets and the production approval) work in private
# repos only on paid plans; on GitHub Free, GitHub ignores them. Stop before
# changing anything rather than leave a deploy path that cannot work.
if [[ $VISIBILITY != PUBLIC ]]; then
  OWNER=${REPO%%/*}
  if [[ ${OWNER,,} == "${ME_LOGIN,,}" ]]; then
    PLAN=$ME_PLAN
  else
    PLAN=$(gh api "orgs/$OWNER" --jq '.plan.name // "unknown"' 2>/dev/null || echo unknown)
  fi
  if [[ $PLAN == free ]]; then
    die "$REPO is private on GitHub Free, where GitHub ignores environment secrets and approvals.
  Make it public:  gh repo edit $REPO --visibility public --accept-visibility-change-consequences
  or move it to a paid plan (GitHub Pro or Team), then run this again."
  elif [[ $PLAN == unknown ]]; then
    printf '  warning: cannot tell %s'"'"'s plan; private repos need GitHub Pro or Team for deploy environments\n' "$OWNER" >&2
  fi
fi

log "Workflow: .github/workflows/deploy.yml"
[[ -f $DIR/Containerfile ]] || printf '  note: no Containerfile in %s yet; the build job needs one (see docs/deploy.md)\n' "$DIR"
if (( ! DRY_RUN )); then
  mkdir -p "$DIR/.github/workflows"
  cp "$WORKFLOW_SOURCE" "$DIR/.github/workflows/deploy.yml"
else
  printf '  + copy %s to %s\n' "$WORKFLOW_SOURCE" "$DIR/.github/workflows/deploy.yml"
fi

# Create or update an environment that only the default branch may deploy from.
setup_environment() {
  local env=$1 body
  if [[ $env == production ]]; then
    # No wait_timer: some plans refuse the setting outright, even at 0.
    body=$(printf '{"prevent_self_review": false, "reviewers": [{"type": "User", "id": %s}], "deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}' "$ME_ID")
  else
    body='{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}'
  fi
  if (( DRY_RUN )); then
    printf '  + gh api -X PUT repos/%s/environments/%s  %s\n' "$REPO" "$env" "$body"
    printf '  + allow deploys from %s only\n' "$DEFAULT_BRANCH"
    return
  fi
  local out
  if ! out=$(printf '%s' "$body" | gh api -X PUT "repos/$REPO/environments/$env" --input - 2>&1); then
    if [[ $env == production ]]; then
      printf '  warning: could not require your approval for production: %s\n' "$out" >&2
      printf '  (required reviewers need a public repo or GitHub Pro; production would deploy unapproved)\n' >&2
      return 1
    fi
    die "could not create the $env environment: $out"
  fi
  gh api -X POST "repos/$REPO/environments/$env/deployment-branch-policies" \
    -f name="$DEFAULT_BRANCH" -f type=branch >/dev/null 2>&1 || true  # already there on re-runs
}

log "Environments"
setup_environment staging
if ! setup_environment production; then
  die "stopped before creating deploy keys: without an approval gate, every merge would reach production"
fi

if [[ -n $RUNNER ]]; then
  log "Deploy jobs run on your runner (label $RUNNER)"
  if [[ $VISIBILITY == PUBLIC ]]; then
    # A runner inside your network must never run a stranger's workflow.
    if ! run gh api -X PUT "repos/$REPO/actions/permissions/fork-pr-contributor-approval" \
        -f approval_policy=all_external_contributors --silent; then
      die "could not make outside contributors' workflows wait for your approval. Set it in
  Settings > Actions > General > Approval for running fork pull request workflows,
  then run this again"
    fi
    printf "  outside contributors' workflows now wait for your approval\n"
  fi
  run gh variable set DEPLOY_RUNNER --repo "$REPO" --body "$RUNNER"
fi

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
declare -A HOST_COMMAND

for env in staging production; do
  target=$STAGING
  [[ $env == production ]] && target=$PRODUCTION
  host=${target#*@}
  log "Deploy key and host keys for $env ($target)"
  if (( DRY_RUN )); then
    printf '  + ssh-keygen -t ed25519 (new key); gh secret set DEPLOY_SSH_KEY --env %s\n' "$env"
    printf '  + ssh-keyscan %s; gh secret set DEPLOY_KNOWN_HOSTS --env %s\n' "$host" "$env"
    printf '  + gh variable set DEPLOY_HOST --env %s --body %s\n' "$env" "$target"
    HOST_COMMAND[$env]="sudo scripts/setup-deploy-host.sh --app $APP --image $IMAGE --port $PORT --health-path $HEALTH_PATH --ci-key \"<new $env public key>\""
    continue
  fi
  ssh-keygen -q -t ed25519 -N '' -C "claude-harness deploy $REPO $env" -f "$TMP/$env"
  ssh-keyscan -T 10 "$host" 2>/dev/null | grep -v '^#' > "$TMP/$env.hosts" || true
  [[ -s $TMP/$env.hosts ]] || die "could not read $host's SSH host keys (is it reachable on port 22?)"
  printf '  %s host keys (compare with: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub, on the server):\n' "$host"
  ssh-keygen -lf "$TMP/$env.hosts" | sed 's/^/    /'
  gh secret set DEPLOY_SSH_KEY --repo "$REPO" --env "$env" < "$TMP/$env"
  gh secret set DEPLOY_KNOWN_HOSTS --repo "$REPO" --env "$env" < "$TMP/$env.hosts"
  gh variable set DEPLOY_HOST --repo "$REPO" --env "$env" --body "$target"
  HOST_COMMAND[$env]="sudo scripts/setup-deploy-host.sh --app $APP --image $IMAGE --port $PORT --health-path $HEALTH_PATH --ci-key \"$(cut -d' ' -f1,2 "$TMP/$env.pub")\""
done

log "Next steps"
step=1
if [[ -n $RUNNER ]]; then
  cat <<EOF
$step. On a machine inside your network that reaches both servers (it can be this
   one), with the harness cloned there, as a user whose gh can administer $REPO:

     gh api -X POST repos/$REPO/actions/runners/registration-token --jq .token \\
       | sudo scripts/setup-deploy-runner.sh --repo $REPO --label $RUNNER

EOF
  step=$((step + 1))
fi
cat <<EOF
$step. On each server, with the harness cloned there (git clone https://github.com/soncsec/claude-harness):

   staging ($STAGING):
     ${HOST_COMMAND[staging]}

   production ($PRODUCTION):
     ${HOST_COMMAND[production]}
EOF
if [[ $VISIBILITY != PUBLIC ]]; then
  cat <<EOF

   $REPO is private, so the servers need to log in to pull its image: add
     --pull-user <your login> --pull-token-file <file>
   with a classic token that has only the read:packages scope.
EOF
else
  cat <<EOF

   After the first build, make the package public (GitHub > your profile >
   Packages > ${REPO#*/} > Package settings), or add --pull-user/--pull-token-file
   with a read:packages token.
EOF
fi
cat <<EOF

$((step + 1)). Commit .github/workflows/deploy.yml through a pull request and merge it.
   The merge builds the image and deploys to staging; production waits for
   your approval in the Actions tab.
EOF
