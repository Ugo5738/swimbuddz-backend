#!/bin/bash
set -e

# ===============================================================================
# GENERATE MIGRATION
# ===============================================================================
#
# This script generates a new Alembic migration after you've modified models.
# Use this when:
#   - You've added/modified/removed fields in a model
#   - You've created new models
#   - You've changed relationships or indexes
#
# Usage:
#   ./scripts/db/migrate.sh <service> "<description>"
#   ./scripts/db/migrate.sh members_service "add phone field"
#   ./scripts/db/migrate.sh --all "add audit fields"
#
# After generating:
#   1. Review the generated migration file
#   2. Test locally with: ./scripts/db/reset.sh dev
#   3. Commit the migration file to git
#
# ===============================================================================

print_usage() {
  echo "Usage: $0 <service|--all> \"<description>\""
  echo ""
  echo "Examples:"
  echo "  $0 members_service \"add phone number field\""
  echo "  $0 payments_service \"add invoice table\""
  echo "  $0 --all \"add created_by to all tables\""
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
# SETUP
# -------------------------------------------------------------------------------

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Load environment (default to dev for migrations)
# NOTE: We explicitly set ENV_FILE here, ignoring any inherited value
ENV_FILE=".env.dev"
ENV_PATH="$PROJECT_ROOT/$ENV_FILE"

if [ -f "$ENV_PATH" ]; then
  set -a
  source "$ENV_PATH"
  set +a
  echo "Using environment: $ENV_FILE"
  echo "ENVIRONMENT=$ENVIRONMENT"
  echo ""
fi

# Use session URL for migrations
if [ -n "$DATABASE_SESSION_URL" ]; then
    export DATABASE_URL="$DATABASE_SESSION_URL"
elif [ -n "$DATABASE_TRANSACTION_URL" ]; then
    export DATABASE_URL="$DATABASE_TRANSACTION_URL"
fi

cd "$PROJECT_ROOT"

# -------------------------------------------------------------------------------
# PARSE ARGUMENTS
# -------------------------------------------------------------------------------

if [ $# -lt 2 ]; then
  print_usage
  exit 1
fi

SERVICE="$1"
DESCRIPTION="$2"

# Validate description
if [ -z "$DESCRIPTION" ]; then
  echo "❌ Error: Description is required"
  print_usage
  exit 1
fi

# Sanitize description for filename (lowercase, underscores)
SAFE_DESC=$(echo "$DESCRIPTION" | tr '[:upper:]' '[:lower:]' | tr ' ' '_' | tr -cd '[:alnum:]_')

# -------------------------------------------------------------------------------
# GENERATE MIGRATION(S)
# -------------------------------------------------------------------------------

generate_for_service() {
  local svc="$1"
  local desc="$2"

  ALEMBIC_INI="services/${svc}/alembic.ini"

  if [ ! -f "$ALEMBIC_INI" ]; then
    echo "  ⚠️  No alembic.ini found for $svc, skipping"
    return 0
  fi

  echo "Generating migration for $svc..."

  # Run autogenerate
  OUTPUT=$(alembic -c "$ALEMBIC_INI" revision --autogenerate -m "$desc" 2>&1)

  # Check if there were actual changes
  if echo "$OUTPUT" | grep -q "No changes in schema detected"; then
    echo "  ℹ️  No schema changes detected for $svc"
    return 0
  fi

  # Extract the generated file path
  MIGRATION_FILE=$(echo "$OUTPUT" | grep -oE "Generating .*/versions/[a-f0-9]+_.*\.py" | sed 's/Generating //')

  if [ -n "$MIGRATION_FILE" ]; then
    echo "  ✓ Created: $(basename "$MIGRATION_FILE")"
    echo ""
    echo "  📝 Review this file before committing!"
    echo "     $MIGRATION_FILE"
  else
    echo "  ✓ Migration generated"
  fi

  echo ""
}

echo "========================================="
echo "Generate Alembic Migration"
echo "========================================="
echo ""
echo "Description: $DESCRIPTION"
echo ""

if [ "$SERVICE" = "--all" ]; then
  echo "Generating migrations for ALL services..."
  echo ""

  for svc in "${SERVICES[@]}"; do
    generate_for_service "$svc" "$SAFE_DESC"
  done

else
  # Validate service name
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

  generate_for_service "$SERVICE" "$SAFE_DESC"
fi

echo "========================================="
echo "Next steps:"
echo "========================================="
echo ""
echo "1. Review the generated migration file(s)"
echo "2. Test locally:"
echo "   ./scripts/db/reset.sh dev"
echo ""
echo "3. Commit the migration file(s) to git"
echo ""
