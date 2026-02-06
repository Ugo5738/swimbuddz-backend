#!/bin/bash
set -e

# ===============================================================================
# APPLY MIGRATIONS (PRODUCTION SAFE)
# ===============================================================================
#
# This script applies existing Alembic migrations to a target database.
# It does NOT generate new migrations and does NOT reset/drop data.
#
# Usage:
#   ./scripts/db/migrate-prod.sh <service|--all> [env_file]
#   ./scripts/db/migrate-prod.sh members_service
#   ./scripts/db/migrate-prod.sh --all .env.prod
#
# Default env file: .env.prod
#
# ===============================================================================

print_usage() {
  echo "Usage: $0 <service|--all> [env_file]"
  echo ""
  echo "Examples:"
  echo "  $0 members_service"
  echo "  $0 --all .env.prod"
  echo ""
  echo "Available services:"
  for svc in "${SERVICES[@]}"; do
    echo "  - $svc"
  done
}

SERVICES=(
  "members_service"
  "academy_service"
  "attendance_service"
  "communications_service"
  "events_service"
  "media_service"
  "payments_service"
  "sessions_service"
  "transport_service"
  "store_service"
  "ai_service"
)

# -------------------------------------------------------------------------------
# SAFETY: Clear potentially polluted environment variables
# -------------------------------------------------------------------------------
unset DATABASE_URL
unset DATABASE_DIRECT_URL
unset DATABASE_TRANSACTION_URL
unset DATABASE_SESSION_URL
unset SUPABASE_URL
unset SUPABASE_SERVICE_KEY
unset SUPABASE_ANON_KEY
unset ENVIRONMENT

# -------------------------------------------------------------------------------
# SETUP
# -------------------------------------------------------------------------------
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

if [ $# -lt 1 ]; then
  print_usage
  exit 1
fi

SERVICE="$1"
ENV_FILE="${2:-.env.prod}"
ENV_PATH="$PROJECT_ROOT/$ENV_FILE"

if [ ! -f "$ENV_PATH" ]; then
  echo "❌ Env file not found at $ENV_PATH"
  exit 1
fi

set -a
source "$ENV_PATH"
set +a

echo "Using environment: $ENV_FILE"
echo "ENVIRONMENT=${ENVIRONMENT:-unknown}"
echo ""

# Safety guard: refuse to run unless explicitly production
if [ "${ENVIRONMENT:-}" != "production" ]; then
  echo "❌ Refusing to run migrations: ENVIRONMENT is not 'production'." >&2
  exit 1
fi

# Prefer session URL for migrations (matches other scripts)
if [ -n "$DATABASE_SESSION_URL" ]; then
  export DATABASE_URL="$DATABASE_SESSION_URL"
elif [ -n "$DATABASE_TRANSACTION_URL" ]; then
  export DATABASE_URL="$DATABASE_TRANSACTION_URL"
fi

cd "$PROJECT_ROOT"

run_migration() {
  local svc="$1"
  local alembic_ini="services/${svc}/alembic.ini"

  if [ ! -f "$alembic_ini" ]; then
    echo "  ⚠️  Missing $alembic_ini; skipping"
    return 0
  fi

  echo "Applying migrations for $svc..."
  alembic -c "$alembic_ini" upgrade head
  echo "  ✓ Done"
  echo ""
}

echo "========================================="
echo "Apply Alembic Migrations"
echo "========================================="
echo ""

if [ "$SERVICE" = "--all" ]; then
  for svc in "${SERVICES[@]}"; do
    run_migration "$svc"
  done
else
  VALID=false
  for svc in "${SERVICES[@]}"; do
    if [ "$svc" = "$SERVICE" ]; then
      VALID=true
      break
    fi
  done

  if [ "$VALID" = false ]; then
    echo "❌ Error: Unknown service '$SERVICE'"
    echo ""
    print_usage
    exit 1
  fi

  run_migration "$SERVICE"
fi

echo "========================================="
echo "All migrations complete."
echo "========================================="
