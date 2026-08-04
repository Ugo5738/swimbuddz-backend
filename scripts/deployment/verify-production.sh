#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "$SCRIPT_DIR/_common.sh"

WORKERS_EXPECTED="${WORKERS_EXPECTED:-true}"
enter_deploy_path

log "Checking Redis"
redis_result="$(docker exec swimbuddz_redis redis-cli ping 2>/dev/null || true)"
[ "$redis_result" = "PONG" ] || die "Redis health check failed. Received: ${redis_result:-nothing}"

log "Checking gateway health"
gateway_ok=false
for attempt in $(seq 1 30); do
  if docker exec swimbuddz_gateway python -c \
    'import json, urllib.request; d=json.load(urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5)); assert d["status"]=="ok"' \
    >/dev/null 2>&1; then
    gateway_ok=true
    break
  fi
  sleep 5
done
[ "$gateway_ok" = "true" ] || die "Gateway did not become healthy."

log "Checking for failed or restarting SwimBuddz containers"
bad_containers="$(
  docker ps -a \
    --filter 'name=swimbuddz_' \
    --format '{{.Names}}|{{.Status}}' |
  awk -F'|' '$2 ~ /Exited|Restarting|Dead/ {print}'
)"
if [ -n "$bad_containers" ]; then
  printf '%s\n' "$bad_containers" >&2
  die "One or more SwimBuddz containers are unhealthy."
fi

if [ "$WORKERS_EXPECTED" = "true" ]; then
  log "Checking that every expected worker is running"
  for service in "${WORKER_SERVICES[@]}"; do
    container_id="$(compose ps -q "$service")"
    [ -n "$container_id" ] || die "Worker service has no container: $service"
    running="$(docker inspect -f '{{.State.Running}}' "$container_id")"
    [ "$running" = "true" ] || die "Worker is not running: $service"
  done
else
  log "Workers were intentionally not required for this deployment"
fi

log "Verification passed"
docker ps --filter 'name=swimbuddz_' --format 'table {{.Names}}\t{{.Status}}'
