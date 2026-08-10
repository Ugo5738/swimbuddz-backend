#!/usr/bin/env bash
set -Eeuo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-/home/deploy/swimbuddz-backend}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-swimbuddz-backend}"

export COMPOSE_PROJECT_NAME

WORKER_SERVICES=(
  academy-worker
  members-worker
  events-worker
  transport-worker
  communications-worker
  volunteer-worker
  payments-worker
  media-worker
  sessions-worker
  reporting-worker
  corporate-worker
  ledger-worker
  attendance-worker
  pools-worker
  ai-worker
  ai-worker-public
)

log() {
  printf '\n[%s] %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$*"
}

die() {
  printf '\nERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

enter_deploy_path() {
  [ -d "$DEPLOY_PATH" ] || die "Deployment directory does not exist: $DEPLOY_PATH"
  cd "$DEPLOY_PATH"
  [ -f "$COMPOSE_FILE" ] || die "Compose file not found: $DEPLOY_PATH/$COMPOSE_FILE"
}

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}
