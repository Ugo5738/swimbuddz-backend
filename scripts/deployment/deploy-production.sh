#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

DEPLOY_BRANCH="${DEPLOY_BRANCH:-main}"
REPO_URL="${REPO_URL:-https://github.com/Ugo5738/swimbuddz-backend.git}"
GPG_PASSPHRASE="${GPG_PASSPHRASE:-}"
DOCKER_USERNAME="${DOCKER_USERNAME:-}"
DOCKER_PASSWORD="${DOCKER_PASSWORD:-}"
LE_EMAIL="${LE_EMAIL:-}"
API_HOST="${API_HOST:-api.swimbuddz.com}"
START_WORKERS="${START_WORKERS:-true}"
DOCKER_API_VERSION="${DOCKER_API_VERSION:-1.52}"
COMPOSE_PARALLEL_LIMIT="${COMPOSE_PARALLEL_LIMIT:-1}"

export DOCKER_USERNAME LE_EMAIL API_HOST DOCKER_API_VERSION COMPOSE_PARALLEL_LIMIT

require_command git
require_command gpg
require_command docker

[ -n "$GPG_PASSPHRASE" ] || die "GPG_PASSPHRASE is required."
[ -n "$DOCKER_USERNAME" ] || die "DOCKER_USERNAME is required."
[ -n "$DOCKER_PASSWORD" ] || die "DOCKER_PASSWORD is required."
[ -n "$LE_EMAIL" ] || die "LE_EMAIL is required."

mkdir -p "$DEPLOY_PATH"
cd "$DEPLOY_PATH"

log "Synchronising repository branch: $DEPLOY_BRANCH"
if [ ! -d .git ]; then
  git clone --branch "$DEPLOY_BRANCH" "$REPO_URL" .
else
  git remote set-url origin "$REPO_URL"
  git fetch origin "$DEPLOY_BRANCH"
  git checkout "$DEPLOY_BRANCH"
  git reset --hard "origin/$DEPLOY_BRANCH"
fi

[ -f .env.prod.gpg ] || die "Missing .env.prod.gpg in the repository."

log "Decrypting production environment"
tmp_env="$(mktemp "$DEPLOY_PATH/.env.prod.XXXXXX")"
cleanup() {
  rm -f "$tmp_env"
}
trap cleanup EXIT

printf '%s' "$GPG_PASSPHRASE" | \
  gpg --batch --yes --passphrase-fd 0 --output "$tmp_env" --decrypt .env.prod.gpg
chmod 600 "$tmp_env"
mv "$tmp_env" .env.prod
trap - EXIT

log "Validating Docker Compose configuration"
docker network inspect swimbuddz_proxy >/dev/null 2>&1 || docker network create swimbuddz_proxy
mkdir -p letsencrypt
touch letsencrypt/acme.json
chmod 600 letsencrypt/acme.json
compose config --quiet

log "Logging in to Docker Hub"
printf '%s' "$DOCKER_PASSWORD" | \
  docker login --username "$DOCKER_USERNAME" --password-stdin

log "Pulling production images"
compose pull

log "Running production database migrations"
compose run --rm --no-deps gateway \
  ./scripts/db/migrate-prod.sh --all .env.prod

if [ "$START_WORKERS" = "true" ]; then
  log "Starting the full production stack, including workers"
  compose up -d --wait
else
  log "Starting core services only; workers remain off"
  # gateway starts all of its domain-service dependencies. Redis and Traefik
  # are named explicitly. Background workers are intentionally omitted.
  compose up -d --wait traefik redis gateway
fi

log "Running local deployment verification"
WORKERS_EXPECTED="$START_WORKERS" "$SCRIPT_DIR/verify-production.sh"

log "Removing unused Docker images"
docker image prune -f

log "Deployment completed successfully"
printf '\nAPI host: %s\nWorkers started: %s\n' "$API_HOST" "$START_WORKERS"
