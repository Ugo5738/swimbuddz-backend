#!/bin/bash
set -e

# ===============================================================================
# FULL RESET (Nuclear Option - Regenerates Migrations)
# ===============================================================================
#
# ⚠️  WARNING: This script DELETES and REGENERATES all migration files!
#
# Use this ONLY for:
#   - Initial project setup
#   - Major schema refactors where existing migrations are broken
#   - Starting fresh after significant model changes
#
# For normal development:
#   → Reset DB (keep migrations): ./scripts/db/reset.sh
#   → Generate new migration:     ./scripts/db/migrate.sh
#
# ===============================================================================

echo "========================================="
echo "SwimBuddz FULL Reset (Nuclear Option)"
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
  "clear_supabase_users:Clear Supabase Auth users"
  "create_admin:Create admin user"
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
  # Autogenerate NEW migration (this is what makes full_reset different)
  alembic -c "$ALEMBIC_INI" revision --autogenerate -m "initial_migration"
  alembic -c "$ALEMBIC_INI" upgrade head
}

task_clear_supabase_users() {
  python3 scripts/auth/clear-users.py
}

task_create_admin() {
  python3 scripts/auth/create-admin.py
}

task_seed_all() {
  "$SCRIPT_DIR/../seed/all.sh"
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
      echo "⚠️  FULL RESET - Regenerates all migrations!"
      echo ""
      echo "This script will run ${TOTAL_STEPS} steps:"
      echo "  - ${#PRE_MIGRATION_TASKS[@]} pre-migration tasks (incl. DELETE migrations)"
      echo "  - ${#SERVICES[@]} service migrations (REGENERATED)"
      echo "  - ${#POST_MIGRATION_TASKS[@]} post-migration tasks"
      echo ""
      echo "For normal development, use instead:"
      echo "  ./scripts/db/reset.sh    - Reset DB, keep migrations"
      echo "  ./scripts/db/migrate.sh  - New migration after model changes"
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
  echo "⚠️  WARNING: You are about to FULL RESET the PRODUCTION database!"
  echo "   This will DELETE all migration files and regenerate them."
  echo ""
  read -p "Type 'yes-delete-migrations' to confirm: " CONFIRM
  if [ "$CONFIRM" != "yes-delete-migrations" ]; then
    echo "Aborted."
    exit 1
  fi
  echo ""
fi

# Database URL
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
print_header "PHASE 3: POST-MIGRATION"

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
echo "✓ Full reset complete! (${TOTAL_STEPS} steps)"
echo "========================================="
echo ""
echo "⚠️  Remember: Migration files were REGENERATED."
echo "   If you commit these, they will differ from previous runs."
echo ""
echo "You can now log in with:"
echo "  Email:    admin@admin.com"
echo "  Password: admin"
echo ""
