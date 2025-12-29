#!/bin/bash
set -e  # Exit on error

echo "========================================="
echo "SwimBuddz Full Database Reset Script"
echo "========================================="
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Allow ENV_FILE override via first arg (dev|prod|path) or ENV_FILE env var.
if [ -n "${1:-}" ]; then
  case "$1" in
    dev) ENV_FILE=".env.dev" ;;
    prod) ENV_FILE=".env.prod" ;;
    -h|--help)
      echo "Usage: $0 [dev|prod|path/to/env]" 
      exit 0
      ;;
    *) ENV_FILE="$1" ;;  # treat as explicit path relative to repo
  esac
else
  ENV_FILE=${ENV_FILE:-.env.prod}
fi

# Load env file so every command hits the same DB/Supabase project
ENV_PATH="$PROJECT_ROOT/$ENV_FILE"

# Ensure child python scripts (nuke_db.py) pick up the selected env
export ENV_FILE

if [ ! -f "$ENV_PATH" ]; then
    echo "Env file not found at $ENV_PATH"
    exit 1
fi

set -a  # export vars loaded from the env file
source "$ENV_PATH"
set +a

echo "Using environment from $ENV_PATH"
echo "ENVIRONMENT=$ENVIRONMENT"

# Prefer session pooler for migrations (DDL operations need stable connections)
# Session mode (port 5432) is better than transaction mode (port 6543) for schema changes
if [ -n "$DATABASE_SESSION_URL" ]; then
    export DATABASE_URL="$DATABASE_SESSION_URL"
    echo "Using DATABASE_SESSION_URL for migrations (session mode)"
elif [ -n "$DATABASE_TRANSACTION_URL" ]; then
    export DATABASE_URL="$DATABASE_TRANSACTION_URL"
    echo "Using DATABASE_TRANSACTION_URL for migrations (fallback)"
fi

# Determine which docker-compose file to use based on environment
if [ "$ENV_FILE" = ".env.prod" ] || [ "$ENV_FILE" = "prod" ]; then
    COMPOSE_FILE="docker-compose.prod.yml"
else
    COMPOSE_FILE="docker-compose.yml"
fi
echo "Using Docker Compose file: $COMPOSE_FILE"

cd "$PROJECT_ROOT"

# Check if we're inside Docker or running locally
if [ -f /.dockerenv ]; then
    # Running inside Docker container
    INSIDE_DOCKER=true
    echo "Running inside Docker container"
else
    # Running locally - need to stop/start services if docker is available
    INSIDE_DOCKER=false
    echo "Running locally"
    
    # Check if docker is available
    if command -v docker &> /dev/null; then
        echo "Step 0/6: Stopping all services..."
        docker compose -f "$COMPOSE_FILE" down 2>/dev/null || echo "Note: Could not stop docker services (may not be running)"
        echo "✓ Services stop attempted"
        echo ""
    else
        echo "Note: Docker not found in PATH, skipping service management"
        echo ""
    fi
fi

# Step 1: Clean __pycache__ directories
echo "Step 1/7: Cleaning __pycache__ directories..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
echo "✓ Python cache cleaned"
echo ""

# Step 2: Nuke the database
echo "Step 2/7: Dropping database schema..."
python3 scripts/database/nuke_db.py
echo "✓ Database schema dropped"
echo ""

# Step 3: Generate and apply per-service migrations
# Note: members_service must run first because other services reference the members table.
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
)

step=3
total_steps=$((3 + ${#SERVICES[@]}))

for svc in "${SERVICES[@]}"; do
  VERSIONS_DIR="services/${svc}/alembic/versions"
  if [ -d "$VERSIONS_DIR" ]; then
    find "$VERSIONS_DIR" -type f -name "*.py" ! -name ".keep" -delete
  fi
done
echo "✓ Old migrations cleaned"
echo ""

for svc in "${SERVICES[@]}"; do
  echo "Step ${step}/${total_steps}: Migrating $svc..."
  ALEMBIC_INI="services/${svc}/alembic.ini"
  if [ ! -f "$ALEMBIC_INI" ]; then
    echo "  ✗ Missing $ALEMBIC_INI; skipping"
  else
    # Autogenerate and apply
    alembic -c "$ALEMBIC_INI" revision --autogenerate -m "initial_migration"
    alembic -c "$ALEMBIC_INI" upgrade head
    echo "  ✓ $svc migrated"
  fi
  echo ""
  step=$((step+1))
done

# Step N: Clear Supabase Auth users
echo "Step ${step}/${total_steps}: Clearing Supabase Auth users..."
python3 scripts/users/clear_supabase_users.py
echo "✓ Supabase users cleared"
echo ""
step=$((step+1))

# Step N+1: Create admin user
echo "Step ${step}/${total_steps}: Creating admin user..."
python3 scripts/users/create_admin.py
echo "✓ Admin user created"
echo ""

# If we stopped services, start them again
if [ "$INSIDE_DOCKER" = false ] && command -v docker &> /dev/null; then
    echo "Restarting services..."
    docker compose -f "$COMPOSE_FILE" up -d 2>/dev/null || echo "Note: Could not restart docker services"
    # Wait for services to be ready
    sleep 5
    # Restart to ensure fresh database connections
    docker compose -f "$COMPOSE_FILE" restart 2>/dev/null || true
    echo "✓ Services started and restarted with fresh database connections"
    echo ""
fi

echo "========================================="
echo "✓ Full reset complete!"
echo "========================================="
echo ""
echo "You can now log in with:"
echo "  Email:    admin@admin.com"
echo "  Password: admin"
echo ""
