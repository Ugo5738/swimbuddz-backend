#!/bin/bash
set -e

# ===============================================================================
# RESET DATABASE (Regenerate Migrations, KEEP AUTH USERS)
# ===============================================================================
#
# This script:
#   - Drops all tables in public schema
#   - Deletes existing migration files
#   - Regenerates initial migrations
#   - Applies migrations
#   - Seeds data
#   - Backfills members from Supabase auth.users
#
# It DOES NOT:
#   - Clear Supabase Auth users
#   - Create admin user
#
# Usage:
#   ./scripts/db/reset-no-auth.sh [dev|prod|path/to/env]
#
# ===============================================================================

echo "========================================="
echo "SwimBuddz Reset (No Auth Deletion)"
echo "========================================="
echo ""

# -------------------------------------------------------------------------------
# SAFETY: Clear potentially polluted environment variables
# This prevents accidentally using a different database than intended
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
# TASK REGISTRY
# -------------------------------------------------------------------------------

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
)

PRE_MIGRATION_TASKS=(
  "clean_pycache:Clean Python cache"
  "nuke_database:Drop database schema"
  "clean_migrations:Delete old migration files"
)

POST_MIGRATION_TASKS=(
  "backfill_auth_members:Backfill members from Supabase auth.users"
  "seed_all:Seed all data"
)

# -------------------------------------------------------------------------------
# TASK FUNCTIONS
# -------------------------------------------------------------------------------

task_clean_pycache() {
  find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
}

task_nuke_database() {
  # Pass --yes flag to skip confirmation for dev, nuke.py will still
  # require manual confirmation for production databases
  if [ "$ENV_FILE" = ".env.dev" ]; then
    python3 scripts/db/nuke.py --yes
  else
    python3 scripts/db/nuke.py
  fi
}

task_clean_migrations() {
  for svc in "${SERVICES[@]}"; do
    VERSIONS_DIR="services/${svc}/alembic/versions"
    if [ -d "$VERSIONS_DIR" ]; then
      find "$VERSIONS_DIR" -type f -name "*.py" ! -name ".keep" -delete
    fi
  done
}

task_migrate_service() {
  local svc="$1"
  ALEMBIC_INI="services/${svc}/alembic.ini"
  if [ ! -f "$ALEMBIC_INI" ]; then
    echo "  ✗ Missing $ALEMBIC_INI; skipping"
    return 1
  fi
  # Autogenerate NEW migration (fresh initial migration)
  alembic -c "$ALEMBIC_INI" revision --autogenerate -m "initial_migration"
  alembic -c "$ALEMBIC_INI" upgrade head
}

task_seed_all() {
  "$SCRIPT_DIR/../seed/all.sh"
}

task_backfill_auth_members() {
  python3 scripts/auth/backfill-members.py
}

# -------------------------------------------------------------------------------
# STEP COUNTER
# -------------------------------------------------------------------------------

calculate_total_steps() {
  local total=0
  total=$((total + ${#PRE_MIGRATION_TASKS[@]}))
  total=$((total + ${#SERVICES[@]}))
  total=$((total + ${#POST_MIGRATION_TASKS[@]}))
  echo $total
}

CURRENT_STEP=0
TOTAL_STEPS=$(calculate_total_steps)

run_step() {
  local description="$1"
  local success_msg="$2"
  shift 2

  CURRENT_STEP=$((CURRENT_STEP + 1))
  echo "Step ${CURRENT_STEP}/${TOTAL_STEPS}: ${description}..."

  "$@"

  echo "✓ ${success_msg}"
  echo ""
}

print_header() {
  echo ""
  echo "=================================================================="
  echo "  $1"
  echo "=================================================================="
}

# -------------------------------------------------------------------------------
# SETUP & ENVIRONMENT
# -------------------------------------------------------------------------------
print_header "SETUP & ENVIRONMENT"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Parse first argument to determine environment
# NOTE: We explicitly set ENV_FILE here, ignoring any inherited value
if [ -n "${1:-}" ]; then
  case "$1" in
    dev) ENV_FILE=".env.dev" ;;
    prod) ENV_FILE=".env.prod" ;;
    -h|--help)
      echo "Usage: $0 [dev|prod|path/to/env]"
      echo ""
      echo "Reset database using regenerated migrations (NO auth deletion)"
      exit 0
      ;;
    *) ENV_FILE="$1" ;;
  esac
else
  # Default to dev - DO NOT use ${ENV_FILE:-} here to prevent env pollution
  ENV_FILE=".env.dev"
fi

ENV_PATH="$PROJECT_ROOT/$ENV_FILE"
export ENV_FILE

if [ ! -f "$ENV_PATH" ]; then
    echo "❌ Env file not found at $ENV_PATH"
    exit 1
fi

set -a
source "$ENV_PATH"
set +a

echo "Using environment: $ENV_FILE"
echo "ENVIRONMENT=$ENVIRONMENT"

# -------------------------------------------------------------------------------
# SAFETY CHECK: Verify we're using the expected database
# -------------------------------------------------------------------------------
print_header "DATABASE VERIFICATION"

# Extract project ID from DATABASE_SESSION_URL or DATABASE_URL
DB_URL="${DATABASE_SESSION_URL:-$DATABASE_URL}"
if [ -n "$DB_URL" ]; then
    # Extract Supabase project ID (format: postgres.PROJECT_ID@...)
    PROJECT_ID=$(echo "$DB_URL" | grep -oE 'postgres\.[a-z]+' | sed 's/postgres\.//' || echo "unknown")

    # Extract region from URL
    if echo "$DB_URL" | grep -q "eu-west-1"; then
        REGION="eu-west-1 (Ireland) - PRODUCTION"
    elif echo "$DB_URL" | grep -q "eu-central-1"; then
        REGION="eu-central-1 (Frankfurt) - DEV"
    else
        REGION="unknown"
    fi

    echo "Project ID: $PROJECT_ID"
    echo "Region: $REGION"
    echo ""

    # Extra warning for production
    if [ "$ENV_FILE" = ".env.prod" ] || echo "$DB_URL" | grep -q "eu-west-1"; then
        echo "⚠️  WARNING: This appears to be a PRODUCTION database!"
        echo ""
    fi
fi

echo "Total steps: ${TOTAL_STEPS}"

# -------------------------------------------------------------------------------
# CONFIRMATION (for prod)
# -------------------------------------------------------------------------------
if [ "$ENV_FILE" = ".env.prod" ] || [ "$ENV_FILE" = "prod" ]; then
  echo ""
  echo "⚠️  WARNING: You are about to RESET the PRODUCTION database!"
  echo "   This will DELETE all migration files and regenerate them."
  echo "   Supabase Auth users will NOT be deleted."
  echo ""
  read -p "Type 'yes-reset-no-auth' to confirm: " CONFIRM
  if [ "$CONFIRM" != "yes-reset-no-auth" ]; then
    echo "Aborted."
    exit 1
  fi
  echo ""
fi

# Database URL selection
if [ -n "$DATABASE_SESSION_URL" ]; then
    export DATABASE_URL="$DATABASE_SESSION_URL"
    echo "Using DATABASE_SESSION_URL (session mode)"
elif [ -n "$DATABASE_TRANSACTION_URL" ]; then
    export DATABASE_URL="$DATABASE_TRANSACTION_URL"
    echo "Using DATABASE_TRANSACTION_URL (fallback)"
fi

# Docker compose file
if [ "$ENV_FILE" = ".env.prod" ] || [ "$ENV_FILE" = "prod" ]; then
    COMPOSE_FILE="docker-compose.prod.yml"
else
    COMPOSE_FILE="docker-compose.yml"
fi
echo "Using Docker Compose file: $COMPOSE_FILE"

cd "$PROJECT_ROOT"

# Check Docker
INSIDE_DOCKER=false
if [ -f /.dockerenv ]; then
    INSIDE_DOCKER=true
    echo "Running inside Docker container"
else
    echo "Running locally"
    if command -v docker &> /dev/null; then
        echo "Stopping services..."
        docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
        echo ""
    fi
fi

# -------------------------------------------------------------------------------
# PHASE 1: PRE-MIGRATION
# -------------------------------------------------------------------------------
print_header "PHASE 1: PRE-MIGRATION (Destructive)"

for task_entry in "${PRE_MIGRATION_TASKS[@]}"; do
  IFS=':' read -r func_name description <<< "$task_entry"
  run_step "$description" "$description complete" "task_${func_name}"
done

# -------------------------------------------------------------------------------
# PHASE 2: REGENERATE MIGRATIONS
# -------------------------------------------------------------------------------
print_header "PHASE 2: REGENERATE MIGRATIONS"

for svc in "${SERVICES[@]}"; do
  run_step "Migrating $svc (autogenerate)" "$svc migrated" task_migrate_service "$svc"
done

# -------------------------------------------------------------------------------
# PHASE 3: POST-MIGRATION
# -------------------------------------------------------------------------------
print_header "PHASE 3: SEED & BACKFILL"

for task_entry in "${POST_MIGRATION_TASKS[@]}"; do
  IFS=':' read -r func_name description <<< "$task_entry"
  run_step "$description" "$description complete" "task_${func_name}"
done

# -------------------------------------------------------------------------------
# RESTART SERVICES
# -------------------------------------------------------------------------------
if [ "$INSIDE_DOCKER" = false ] && command -v docker &> /dev/null; then
    print_header "RESTARTING SERVICES"
    docker compose -f "$COMPOSE_FILE" up -d 2>/dev/null || true
    sleep 5
    docker compose -f "$COMPOSE_FILE" restart 2>/dev/null || true
    echo "✓ Services restarted"
    echo ""
fi

# -------------------------------------------------------------------------------
# DONE
# -------------------------------------------------------------------------------
echo "========================================="
echo "✓ Reset complete! (${TOTAL_STEPS} steps)"
echo "========================================="
echo ""
echo "Supabase Auth users were preserved."
echo ""
