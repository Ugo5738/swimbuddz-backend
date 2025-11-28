#!/bin/bash
set -e  # Exit on error

echo "========================================="
echo "SwimBuddz Full Database Reset Script"
echo "========================================="
echo ""

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

# Step 1: Nuke the database
echo "Step 1/5: Dropping database schema..."
python3 scripts/database/nuke_db.py
echo "✓ Database schema dropped"
echo ""

# Step 2: Delete migration files
echo "Step 2/5: Deleting old migration files..."
rm -f alembic/versions/*.py
echo "✓ Migration files deleted"
echo ""

# Step 3: Generate new initial migration
echo "Step 3/5: Generating new initial migration..."
alembic revision --autogenerate -m "initial_setup"
echo "✓ New migration generated"
echo ""

# Step 4: Apply migration
echo "Step 4/5: Applying migration to database..."
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

echo "========================================="
echo "✓ Full reset complete!"
echo "========================================="
echo ""
echo "You can now log in with:"
echo "  Email:    admin@admin.com"
echo "  Password: admin"
echo ""
