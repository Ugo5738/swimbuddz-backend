#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

enter_deploy_path

log "Stopping SwimBuddz background workers"
compose stop --timeout 90 "${WORKER_SERVICES[@]}"

log "Worker status"
compose ps "${WORKER_SERVICES[@]}"

log "All requested workers have been stopped"
