#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

enter_deploy_path

log "Stopping the complete SwimBuddz stack without deleting containers or volumes"
compose stop --timeout 90

log "Stack stopped. Use deploy-production.sh or docker compose start to restore it."
