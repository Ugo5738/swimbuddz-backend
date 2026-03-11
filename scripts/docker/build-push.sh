#!/usr/bin/env bash
# ==================================================================
# Build & push multi-platform Docker images to Docker Hub
#
# Usage:
#   ./scripts/docker/build-push.sh              # all services
#   ./scripts/docker/build-push.sh academy      # single service
#   ./scripts/docker/build-push.sh academy,gateway  # multiple services
#
# Environment:
#   DOCKER_USERNAME  - Docker Hub username (default: from .env)
#   TAG              - Image tag (default: latest)
#   PLATFORMS        - Target platforms (default: linux/amd64,linux/arm64)
# ==================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Load DOCKER_USERNAME from .env if not set
if [[ -z "${DOCKER_USERNAME:-}" ]] && [[ -f "$PROJECT_ROOT/.env" ]]; then
  DOCKER_USERNAME=$(grep -E '^DOCKER_USERNAME=' "$PROJECT_ROOT/.env" | cut -d= -f2)
fi

if [[ -z "${DOCKER_USERNAME:-}" ]]; then
  echo "Error: DOCKER_USERNAME not set. Export it or add to .env"
  exit 1
fi

TAG="${TAG:-latest}"
PLATFORMS="${PLATFORMS:-linux/amd64,linux/arm64}"

ALL_SERVICES=(
  gateway
  members
  sessions
  attendance
  communications
  payments
  academy
  events
  media
  transport
  store
  ai
  volunteer
  wallet
  pools
)

# Parse service list from argument (comma-separated) or use all
if [[ -n "${1:-}" ]]; then
  IFS=',' read -ra SERVICES <<< "$1"
else
  SERVICES=("${ALL_SERVICES[@]}")
fi

# Use the active Docker Desktop builder — it supports multi-platform natively on Apple Silicon.
# Only create a container-based builder as a last resort.
ACTIVE_BUILDER=$(docker buildx ls 2>/dev/null | grep '\*' | awk '{print $1}' || echo "")
BUILDER_PLATFORMS=$(docker buildx inspect 2>/dev/null || echo "")

if echo "$BUILDER_PLATFORMS" | grep -q "linux/arm64" && echo "$BUILDER_PLATFORMS" | grep -q "linux/amd64"; then
  echo "Using active builder: ${ACTIVE_BUILDER:-default} (multi-platform supported)"
else
  BUILDER_NAME="swimbuddz-multiplatform"
  if docker buildx inspect "$BUILDER_NAME" &>/dev/null; then
    docker buildx use "$BUILDER_NAME"
  else
    echo "Creating multi-platform builder: $BUILDER_NAME"
    docker buildx create --name "$BUILDER_NAME" --driver docker-container --use --bootstrap
  fi
fi

echo "========================================"
echo "Docker Hub:  $DOCKER_USERNAME"
echo "Tag:         $TAG"
echo "Platforms:   $PLATFORMS"
echo "Services:    ${SERVICES[*]}"
echo "========================================"
echo ""

FAILED=()
SUCCEEDED=()

for service in "${SERVICES[@]}"; do
  service=$(echo "$service" | xargs)  # trim whitespace
  dockerfile="$PROJECT_ROOT/services/${service}_service/Dockerfile"
  image="$DOCKER_USERNAME/swimbuddz-${service}-service:$TAG"

  if [[ ! -f "$dockerfile" ]]; then
    echo "SKIP: $service (no Dockerfile at $dockerfile)"
    FAILED+=("$service")
    continue
  fi

  echo "--- Building & pushing: $image ---"
  if docker buildx build \
    --platform "$PLATFORMS" \
    -t "$image" \
    -f "$dockerfile" \
    --push \
    "$PROJECT_ROOT"; then
    SUCCEEDED+=("$service")
    echo "OK: $image"
  else
    FAILED+=("$service")
    echo "FAIL: $image"
  fi
  echo ""
done

echo "========================================"
echo "DONE"
echo "  Succeeded: ${#SUCCEEDED[@]}/${#SERVICES[@]}"
if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "  Failed:    ${FAILED[*]}"
fi
echo "========================================"
