# SwimBuddz Backend Scripts

## Quick Reference

| I want to... | Command |
|--------------|---------|
| Reset dev DB (daily dev work) | `./scripts/database/reset_db.sh dev` |
| Reset prod DB | `./scripts/database/reset_db.sh prod` |
| Generate migration after model change | `./scripts/database/generate_migration.sh <service> "description"` |
| Regenerate all migrations (nuclear) | `./scripts/full_reset.sh dev` |
| Just seed data | `./scripts/seeding/seed_all.sh dev` |

---

## Database Management Scripts

### 1. `database/reset_db.sh` - Daily Development Reset

**Use when:** You want a fresh database but haven't changed any models.

```bash
./scripts/database/reset_db.sh dev   # Reset dev database
./scripts/database/reset_db.sh prod  # Reset prod database
```

**What it does:**
1. Drops all tables
2. Applies **existing** migrations (from git)
3. Clears Supabase Auth users
4. Creates admin user
5. Runs all seeders

**What it does NOT do:**
- Delete migration files
- Regenerate migrations

This is safe to run frequently. Migration files remain unchanged.

---

### 2. `database/generate_migration.sh` - After Model Changes

**Use when:** You've modified a SQLAlchemy model and need a new migration.

```bash
# Single service
./scripts/database/generate_migration.sh members_service "add phone number field"
./scripts/database/generate_migration.sh payments_service "add invoice table"

# All services (rare)
./scripts/database/generate_migration.sh --all "add audit timestamps"
```

**Workflow:**
1. Modify your model in `services/<service>/models.py`
2. Run `generate_migration.sh <service> "description"`
3. **Review the generated migration file** (important!)
4. Test: `./scripts/database/reset_db.sh dev`
5. Commit the migration file to git

**Available services:**
- members_service
- academy_service
- attendance_service
- communications_service
- events_service
- media_service
- payments_service
- sessions_service
- transport_service
- store_service

---

### 3. `full_reset.sh` - Nuclear Option

**Use ONLY when:**
- Initial project setup
- Major schema refactor where migrations are broken
- Starting completely fresh

```bash
./scripts/full_reset.sh dev   # Dev (no confirmation)
./scripts/full_reset.sh prod  # Prod (requires typing confirmation)
```

**What it does:**
1. Drops all tables
2. **DELETES all migration files**
3. **Regenerates migrations** from current models
4. Applies new migrations
5. Clears Supabase, creates admin, seeds data

⚠️ **Warning:** This regenerates migration files with new hashes. If you commit these, they'll conflict with any other branch that also ran full_reset.

---

### 4. `seeding/seed_all.sh` - Just Seed Data

**Use when:** Database schema is fine, you just need seed data.

```bash
./scripts/seeding/seed_all.sh dev
./scripts/seeding/seed_all.sh prod
```

**Current seeders:**
- Beginner swimming program
- Discount codes
- Content posts

**Adding a new seeder:**
1. Create your script in `scripts/seeding/`
2. Add entry to `SEED_TASKS` in `seed_all.sh`

---

## Directory Structure

```
scripts/
├── README.md                    # This file
├── full_reset.sh                # Nuclear option (regenerates migrations)
│
├── database/
│   ├── reset_db.sh              # Daily reset (uses existing migrations)
│   ├── generate_migration.sh    # Create new migration after model change
│   ├── nuke_db.py               # Python script to drop all tables
│   ├── check_db_tables.py       # List all database tables
│   └── clear_alembic.py         # Reset alembic migration history
│
├── seeding/
│   ├── seed_all.sh              # Run all seeders
│   ├── seed_program.py          # Beginner program seeder
│   ├── seed_discounts.py        # Discount codes seeder
│   ├── seed_content_posts.py    # Content posts seeder
│   └── *.json                   # Seed data files
│
└── users/
    ├── create_admin.py          # Create admin user
    └── clear_supabase_users.py  # Clear all Supabase auth users
```

---

## Typical Development Workflows

### Starting a new feature (no model changes)

```bash
# 1. Reset to clean state
./scripts/database/reset_db.sh dev

# 2. Work on your feature
# 3. Commit your changes
```

### Adding a database field

```bash
# 1. Modify model in services/<service>/models.py
# 2. Generate migration
./scripts/database/generate_migration.sh members_service "add profile_picture_url"

# 3. Review the generated file in services/members_service/alembic/versions/

# 4. Test it
./scripts/database/reset_db.sh dev

# 5. Commit both the model change AND the migration file
git add services/members_service/models.py
git add services/members_service/alembic/versions/*.py
git commit -m "feat(members): add profile picture URL field"
```

### Creating a new table

```bash
# 1. Add model class to services/<service>/models.py
# 2. Generate migration
./scripts/database/generate_migration.sh payments_service "add invoices table"

# 3. Review, test, commit
```

### Switching between branches with different schemas

```bash
# If branches have compatible migrations
./scripts/database/reset_db.sh dev

# If migrations conflict (rare, usually means coordination issue)
./scripts/full_reset.sh dev  # Nuclear - regenerates everything
```

---

## Environment Files

Scripts accept environment as first argument:
- `dev` → `.env.dev`
- `prod` → `.env.prod`
- Custom path → `./path/to/.env.custom`

Default is `.env.dev` for `reset_db.sh` and `generate_migration.sh`.
Default is `.env.prod` for `full_reset.sh` (with confirmation prompt).

---

## Troubleshooting

### "Migration files differ between runs"

This happens when you run `full_reset.sh` - it regenerates migrations with new IDs.

**Solution:** Only use `full_reset.sh` when absolutely necessary. For daily work, use `reset_db.sh`.

### "alembic.util.exc.CommandError: Can't locate revision"

The database's migration history doesn't match available migration files.

**Solution:**
```bash
./scripts/database/reset_db.sh dev  # Usually fixes it
# If not:
./scripts/full_reset.sh dev  # Nuclear option
```

### "No changes in schema detected"

When running `generate_migration.sh`, this means your model changes haven't been detected.

**Check:**
1. Did you save the models.py file?
2. Is the model imported in the alembic env.py?
3. Are you modifying the right service?

---

## Notes

All Python scripts should be run from the project root directory to ensure proper module imports work correctly.
