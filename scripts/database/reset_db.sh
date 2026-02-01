#!/bin/bash
set -e

# ===============================================================================
# RESET DATABASE (Using Existing Migrations)
# ===============================================================================
#
# This script resets the database to a clean state using EXISTING migration files.
# Use this for:
#   - Daily development resets
#   - Testing with fresh data
#   - Switching between dev/prod databases
#
# This script does NOT:
#   - Delete or regenerate migration files
#   - Modify any committed code
#
# For schema changes (after modifying models):
#   → Use: ./scripts/database/generate_migration.sh
#
# For complete rebuild (rare, destructive):
#   → Use: ./scripts/database/full_reset.sh
#
# ===============================================================================

echo "========================================="
echo "SwimBuddz Database Reset"
echo "========================================="
echo ""

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

POST_RESET_TASKS=(
  "clear_supabase_users:Clear Supabase Auth users"
  "create_admin:Create admin user"
  "seed_all:Seed all data"
)

# -------------------------------------------------------------------------------
# TASK FUNCTIONS
# -------------------------------------------------------------------------------

task_nuke_database() {
  python3 scripts/database/nuke_db.py
}

task_migrate_service() {
  local svc="$1"
  ALEMBIC_INI="services/${svc}/alembic.ini"
  if [ ! -f "$ALEMBIC_INI" ]; then
    echo "  ✗ Missing $ALEMBIC_INI; skipping"
    return 1
  fi
  alembic -c "$ALEMBIC_INI" upgrade head
}

task_clear_supabase_users() {
  python3 scripts/users/clear_supabase_users.py
}

task_create_admin() {
  python3 scripts/users/create_admin.py
}

task_seed_all() {
  "$SCRIPT_DIR/../seeding/seed_all.sh"
}

# -------------------------------------------------------------------------------
# STEP COUNTER
# -------------------------------------------------------------------------------

calculate_total_steps() {
  local total=0
  total=$((total + 1))  # nuke database
  total=$((total + ${#SERVICES[@]}))  # migrations
  total=$((total + ${#POST_RESET_TASKS[@]}))  # post-reset tasks
  echo $total
}

CURRENT_STEP=0
TOTAL_STEPS=0  # Calculated after arrays are defined

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

# Allow ENV_FILE override via first arg
if [ -n "${1:-}" ]; then
  case "$1" in
    dev) ENV_FILE=".env.dev" ;;
    prod) ENV_FILE=".env.prod" ;;
    -h|--help)
      TOTAL_STEPS=$(calculate_total_steps)
      echo "Usage: $0 [dev|prod|path/to/env]"
      echo ""
      echo "Reset database using EXISTING migrations (${TOTAL_STEPS} steps)"
      echo ""
      echo "This script:"
      echo "  ✓ Drops all tables"
      echo "  ✓ Applies existing migrations"
      echo "  ✓ Seeds fresh data"
      echo ""
      echo "This script does NOT regenerate migration files."
      echo "For schema changes, use: ./scripts/database/generate_migration.sh"
      exit 0
      ;;
    *) ENV_FILE="$1" ;;
  esac
else
  ENV_FILE=${ENV_FILE:-.env.dev}
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

cd "$PROJECT_ROOT"
TOTAL_STEPS=$(calculate_total_steps)
echo "Total steps: ${TOTAL_STEPS}"

# Check Docker
INSIDE_DOCKER=false
if [ -f /.dockerenv ]; then
    INSIDE_DOCKER=true
else
    if command -v docker &> /dev/null; then
        echo "Stopping services..."
        docker compose -f "$COMPOSE_FILE" down 2>/dev/null || true
        echo ""
    fi
fi

# -------------------------------------------------------------------------------
# PHASE 1: DROP TABLES
# -------------------------------------------------------------------------------
print_header "PHASE 1: DROP TABLES"

run_step "Dropping all tables" "Tables dropped" task_nuke_database

# -------------------------------------------------------------------------------
# PHASE 2: APPLY EXISTING MIGRATIONS
# -------------------------------------------------------------------------------
print_header "PHASE 2: APPLY MIGRATIONS"

for svc in "${SERVICES[@]}"; do
  run_step "Migrating $svc" "$svc migrated" task_migrate_service "$svc"
done

# -------------------------------------------------------------------------------
# PHASE 3: POST-RESET TASKS
# -------------------------------------------------------------------------------
print_header "PHASE 3: SEED DATA"

for task_entry in "${POST_RESET_TASKS[@]}"; do
  IFS=':' read -r func_name description <<< "$task_entry"
  run_step "$description" "$description complete" "task_${func_name}"
done

# -------------------------------------------------------------------------------
# RESTART SERVICES
# -------------------------------------------------------------------------------
if [ "$INSIDE_DOCKER" = false ] && command -v docker &> /dev/null; then
    print_header "RESTARTING SERVICES"
    docker compose -f "$COMPOSE_FILE" up -d 2>/dev/null || true
    sleep 3
    docker compose -f "$COMPOSE_FILE" restart 2>/dev/null || true
    echo "✓ Services restarted"
    echo ""
fi

# -------------------------------------------------------------------------------
# DONE
# -------------------------------------------------------------------------------
echo "========================================="
echo "✓ Database reset complete! (${TOTAL_STEPS} steps)"
echo "========================================="
echo ""
echo "You can now log in with:"
echo "  Email:    admin@admin.com"
echo "  Password: admin"
echo ""
