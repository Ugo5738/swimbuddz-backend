#!/bin/bash
set -e

# ===============================================================================
# SEED ALL DATA
# ===============================================================================
#
# Runs all seeding scripts in the correct order.
# Can be run independently or called from reset_db.sh
#
# Usage:
#   ./scripts/seeding/seed_all.sh [dev|prod]
#
# To add a new seeder:
#   1. Create your script in scripts/seeding/
#   2. Add entry to SEED_TASKS array below
#   3. Done!
#
# ===============================================================================

echo "========================================="
echo "SwimBuddz Data Seeding"
echo "========================================="
echo ""

# -------------------------------------------------------------------------------
# SEED TASK REGISTRY
# -------------------------------------------------------------------------------
# Format: "script_name|args:description"
# Use | to separate script name from arguments
# Order matters - tasks run sequentially

SEED_TASKS=(
  "seed_program.py|--file scripts/seeding/freestyle_beginner.json:Seed Beginner Program"
  "seed_discounts.py|:Seed Discount Codes"
  "seed_content_posts.py|:Seed Content Posts"
  "seed_announcements.py|:Seed Announcements"
)

# -------------------------------------------------------------------------------
# SETUP
# -------------------------------------------------------------------------------

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$(dirname "$SCRIPT_DIR")")"

# Allow ENV_FILE override
if [ -n "${1:-}" ]; then
  case "$1" in
    dev) ENV_FILE=".env.dev" ;;
    prod) ENV_FILE=".env.prod" ;;
    -h|--help)
      echo "Usage: $0 [dev|prod|path/to/env]"
      echo ""
      echo "Runs ${#SEED_TASKS[@]} seeding tasks:"
      for task_entry in "${SEED_TASKS[@]}"; do
        IFS=':' read -r script desc <<< "$task_entry"
        echo "  - $desc"
      done
      exit 0
      ;;
    *) ENV_FILE="$1" ;;
  esac
else
  # Check if ENV_FILE already set (e.g., from parent script)
  ENV_FILE=${ENV_FILE:-.env.dev}
fi

ENV_PATH="$PROJECT_ROOT/$ENV_FILE"

if [ -f "$ENV_PATH" ]; then
  set -a
  source "$ENV_PATH"
  set +a
  echo "Using environment: $ENV_FILE"
fi

# Database URL
if [ -n "$DATABASE_SESSION_URL" ]; then
    export DATABASE_URL="$DATABASE_SESSION_URL"
elif [ -n "$DATABASE_TRANSACTION_URL" ]; then
    export DATABASE_URL="$DATABASE_TRANSACTION_URL"
fi

cd "$PROJECT_ROOT"

# -------------------------------------------------------------------------------
# RUN SEEDERS
# -------------------------------------------------------------------------------

CURRENT=0
TOTAL=${#SEED_TASKS[@]}

for task_entry in "${SEED_TASKS[@]}"; do
  # Split by : first to get command and description
  IFS=':' read -r cmd_part desc <<< "$task_entry"
  # Split command part by | to get script and args
  IFS='|' read -r script_name script_args <<< "$cmd_part"
  CURRENT=$((CURRENT + 1))

  echo ""
  echo "[$CURRENT/$TOTAL] $desc..."
  echo "----------------------------------------"

  # Run the Python script with args (if any)
  if [ -n "$script_args" ]; then
    DATABASE_URL="$DATABASE_URL" python3 "$SCRIPT_DIR/$script_name" $script_args
  else
    DATABASE_URL="$DATABASE_URL" python3 "$SCRIPT_DIR/$script_name"
  fi

  echo "✓ $desc complete"
done

echo ""
echo "========================================="
echo "✓ All seeding complete! ($TOTAL tasks)"
echo "========================================="
echo ""
