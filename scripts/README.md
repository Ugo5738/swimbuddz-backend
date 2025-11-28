# Scripts Directory

Scripts for managing and maintaining the SwimBuddz backend.

## Directory Structure

### `database/`
Database management and inspection tools:
- **check_db_tables.py** - List all database tables
- **clear_alembic.py** - Reset alembic migration history
- **nuke_db.py** - Drop all tables and data (complete reset)

### `users/`
User account management:
- **clear_supabase_users.py** - Remove all users from Supabase
- **create_admin.py** - Create an admin user account

### `seeding/`
Data population scripts:
- **create_dynamic_sessions.py** - Generate session data
- **seed_academy_data.py** - Populate academy programs and cohorts

### Root Scripts
- **full_reset.sh** - Complete system reset (database + Supabase + reseed)

## Usage

### Database Reset
```bash
# Complete nuke (drops all tables)
python scripts/database/nuke_db.py

# Check what tables exist
python scripts/database/check_db_tables.py
```

### User Management
```bash
# Create admin user
python scripts/users/create_admin.py

# Clear all test users
python scripts/users/clear_supabase_users.py
```

### Data Seeding
```bash
# Seed academy data
python scripts/seeding/seed_academy_data.py

# Generate sessions
python scripts/seeding/create_dynamic_sessions.py
```

### Full Reset
```bash
# Reset everything (database + users + reseed)
./scripts/full_reset.sh
```

## Notes

All Python scripts should be run from the project root directory to ensure proper module imports work correctly.
