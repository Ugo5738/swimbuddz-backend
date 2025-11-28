#!/bin/bash
set -e  # Exit on error

echo "========================================="
echo "SwimBuddz Full Database Reset Script"
echo "========================================="
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Load env file so every command hits the same DB/Supabase project
ENV_FILE=${ENV_FILE:-.env.prod}
ENV_PATH="$PROJECT_ROOT/$ENV_FILE"

if [ ! -f "$ENV_PATH" ]; then
    echo "Env file not found at $ENV_PATH"
    exit 1
fi

set -a  # export vars loaded from the env file
source "$ENV_PATH"
set +a

echo "Using environment from $ENV_PATH"
echo "ENVIRONMENT=$ENVIRONMENT"

cd "$PROJECT_ROOT"

# Check if we're inside Docker or running locally
if [ -f /.dockerenv ]; then
    # Running inside Docker container
    INSIDE_DOCKER=true
    echo "Running inside Docker container"
else
    # Running locally - need to stop/start services
    INSIDE_DOCKER=false
    echo "Running locally - will manage Docker services"
    
    # Step 0: Stop all services to release database connections
    echo "Step 0/6: Stopping all services..."
    docker compose -f docker-compose.prod.yml down
    echo "✓ Services stopped"
    echo ""
fi

# Step 1: Nuke the database
echo "Step 1/6: Dropping database schema..."
python3 scripts/database/nuke_db.py
echo "✓ Database schema dropped"
echo ""

# Step 2: Delete migration files
echo "Step 2/6: Deleting old migration files..."
rm -f alembic/versions/*.py
echo "✓ Migration files deleted"
echo ""

# Step 3: Generate new initial migration
echo "Step 3/6: Generating new initial migration..."
alembic revision --autogenerate -m "initial_setup"
echo "✓ New migration generated"
echo ""

# Step 4: Apply migration
echo "Step 4/6: Applying migration to database..."
alembic upgrade head
echo "✓ Migration applied"
echo ""

# Step 5: Clear Supabase Auth users
echo "Step 5/6: Clearing Supabase Auth users..."
python3 scripts/users/clear_supabase_users.py
echo "✓ Supabase users cleared"
echo ""

# Step 6: Create admin user
echo "Step 6/6: Creating admin user..."
python3 scripts/users/create_admin.py
echo "✓ Admin user created"
echo ""

# If we stopped services, start them again
if [ "$INSIDE_DOCKER" = false ]; then
    echo "Step 7/7: Restarting services..."
    docker compose -f docker-compose.prod.yml up -d
    echo "✓ Services restarted"
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
