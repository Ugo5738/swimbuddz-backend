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
  echo "Usage: $0 [--manual] <service|--all> \"<description>\""
  echo ""
  echo "Default behavior runs alembic --autogenerate, which detects most"
  echo "schema changes from your model edits. Pass --manual to create a"
  echo "blank stub instead — required for operations Alembic autogenerate"
  echo "cannot represent: CHECK constraints, RLS policies, raw SQL data"
  echo "migrations, Realtime publication changes, enum-label renames."
  echo ""
  echo "Examples:"
  echo "  $0 members_service \"add phone number field\""
  echo "  $0 payments_service \"add invoice table\""
  echo "  $0 --all \"add created_by to all tables\""
  echo "  $0 --manual sessions_service \"add discriminator check constraint\""
  echo ""
  echo "When you use --manual, fill in upgrade()/downgrade() yourself and"
  echo "ADD the standard \"Hand-written migration — ...\" marker to the"
  echo "docstring per project memory (see feedback_no_handwritten_migrations.md)."
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
  "volunteer_service"
  "wallet_service"
  "pools_service"
  "reporting_service"
  "chat_service"
  "corporate_service"
  "ledger_service"
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

MANUAL_MODE=false
if [ "${1:-}" = "--manual" ]; then
  MANUAL_MODE=true
  shift
fi

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

  if [ "$MANUAL_MODE" = true ]; then
    echo "Generating manual (non-autogenerate) stub for $svc..."
  else
    echo "Generating migration for $svc..."
  fi

  # Run alembic. Use `if !` so a nonzero exit doesn't kill the wrapper
  # silently under `set -e` (the previous form `OUTPUT=$(alembic ... 2>&1)`
  # would abort the function on failure with no diagnostic).
  if [ "$MANUAL_MODE" = true ]; then
    # `alembic revision -m ...` (no --autogenerate) creates an empty stub
    # with an Alembic-assigned revision ID + correct down_revision chain.
    # Author fills in upgrade()/downgrade() themselves. Used for CHECK
    # constraints, RLS, raw SQL, data migrations — anything autogenerate
    # cannot represent.
    if ! OUTPUT=$(alembic -c "$ALEMBIC_INI" revision -m "$desc" 2>&1); then
      echo "  ❌ Alembic failed for $svc:"
      echo "$OUTPUT" | sed 's/^/      /'
      return 1
    fi
  else
    if ! OUTPUT=$(alembic -c "$ALEMBIC_INI" revision --autogenerate -m "$desc" 2>&1); then
      echo "  ❌ Alembic failed for $svc:"
      echo "$OUTPUT" | sed 's/^/      /'
      echo ""
      echo "  Common causes:"
      echo "    * \"Target database is not up to date\" — apply pending migrations first:"
      echo "        alembic -c $ALEMBIC_INI upgrade head"
      echo "    * New model not visible to autogenerate — check alembic env.py"
      echo "      imports + SERVICE_TABLES set"
      echo "    * DB connection / credentials — confirm .env.dev"
      return 1
    fi
  fi

  # Autogenerate mode only: detect "No changes detected"
  if [ "$MANUAL_MODE" != true ] \
     && echo "$OUTPUT" | grep -q "No changes in schema detected"; then
    echo "  ℹ️  No schema changes detected for $svc"
    return 0
  fi

  # Extract the generated file path
  MIGRATION_FILE=$(echo "$OUTPUT" | grep -oE "Generating .*/versions/[a-f0-9]+_.*\.py" | sed 's/Generating //')

  if [ -n "$MIGRATION_FILE" ]; then
    # Autogenerate mode: if alembic produced a file with `pass` bodies it
    # actually meant "no changes" — clean up. Manual mode: an empty stub
    # is the whole point, so leave it alone.
    if [ "$MANUAL_MODE" != true ] \
       && grep -qE "^\s*pass\s*(#|$)" "$MIGRATION_FILE" \
       && ! grep -qE "^\s*op\." "$MIGRATION_FILE"; then
      echo "  ℹ️  No effective schema changes for $svc — removing empty stub:"
      echo "     $(basename "$MIGRATION_FILE")"
      rm -f "$MIGRATION_FILE"
      return 0
    fi
    echo "  ✓ Created: $(basename "$MIGRATION_FILE")"
    echo ""
    if [ "$MANUAL_MODE" = true ]; then
      echo "  ✏️  Manual mode — fill in upgrade()/downgrade() yourself."
      echo "      ADD the standard \"Hand-written migration — …\" docstring"
      echo "      marker so it's clear why autogenerate wasn't used."
    else
      echo "  📝 Review this file before committing!"
    fi
    echo "     $MIGRATION_FILE"
  else
    # Alembic exited 0 but neither expected pattern matched — surface raw
    # output instead of falsely claiming success.
    echo "  ⚠️  Alembic exited 0 but output didn't match expected patterns:"
    echo "$OUTPUT" | sed 's/^/      /'
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
