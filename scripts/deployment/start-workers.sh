#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

enter_deploy_path

log "Starting SwimBuddz background workers"
compose up -d --wait "${WORKER_SERVICES[@]}"

log "Verifying production with workers enabled"
WORKERS_EXPECTED=true "$SCRIPT_DIR/verify-production.sh"

log "Workers started successfully"
