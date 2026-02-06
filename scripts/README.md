# SwimBuddz Backend Scripts

## Quick Reference

| I want to...                          | Command                                           |
| ------------------------------------- | ------------------------------------------------- |
| Reset dev DB (daily dev work)         | `./scripts/db/reset.sh dev`                       |
| Reset prod DB                         | `./scripts/db/reset.sh prod`                      |
| Generate migration after model change | `./scripts/db/migrate.sh <service> "description"` |
| Regenerate all migrations (nuclear)   | `./scripts/db/full-reset.sh dev`                  |
| Just seed data                        | `./scripts/seed/all.sh dev`                       |
| Backup migrations                     | `./scripts/db/backup-migrations.sh`               |
| Generate OpenAPI spec                 | `python scripts/api/generate-openapi.py`          |

---

## Directory Structure

```
scripts/
├── README.md                 # This file
│
├── db/                       # Database management
│   ├── nuke.py               # Drop all tables and enums
│   ├── reset.sh              # Daily reset (uses existing migrations)
│   ├── migrate.sh            # Generate new migration after model change
│   ├── status.py             # List all database tables
│   ├── clear-alembic.py      # Reset alembic migration history
│   ├── backup-migrations.sh  # Backup migration files
│   ├── full-reset.sh         # Nuclear option (regenerates migrations)
│   └── full-reset-all.sh     # Reset both dev and prod databases
│
├── seed/                     # Seeding scripts
│   ├── all.sh                # Run all seeders
│   ├── program.py            # Seed academy programs
│   ├── discounts.py          # Seed discount codes
│   ├── announcements.py      # Seed announcements
│   ├── content-posts.py      # Seed content posts
│   └── clean-program.py      # Clean up program data
│
├── seed-data/                # Seed data files (JSON)
│   ├── freestyle_beginner.json
│   ├── announcements.json
│   └── content_posts.json
│
├── auth/                     # Authentication/user scripts
│   ├── create-admin.py       # Create admin user
│   └── clear-users.py        # Clear all Supabase auth users
│
├── api/                      # API utilities
│   └── generate-openapi.py   # Generate combined OpenAPI schema
│
└── backups/                  # Migration backups (gitignored)
    └── migrations_*/
```

---

## Database Management Scripts

### 1. `db/reset.sh` - Daily Development Reset

**Use when:** You want a fresh database but haven't changed any models.

```bash
./scripts/db/reset.sh dev   # Reset dev database
./scripts/db/reset.sh prod  # Reset prod database
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

### 2. `db/migrate.sh` - After Model Changes

**Use when:** You've modified a SQLAlchemy model and need a new migration.

```bash
# Single service
./scripts/db/migrate.sh members_service "add phone number field"
./scripts/db/migrate.sh payments_service "add invoice table"

# All services (rare)
./scripts/db/migrate.sh --all "add audit timestamps"
```

**Workflow:**

1. Modify your model in `services/<service>/models.py`
2. Run `./scripts/db/migrate.sh <service> "description"`
3. **Review the generated migration file** (important!)
4. Test: `./scripts/db/reset.sh dev`
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

### 3. `db/full-reset.sh` - Nuclear Option

**Use ONLY when:**

- Initial project setup
- Major schema refactor where migrations are broken
- Starting completely fresh

```bash
./scripts/db/full-reset.sh dev   # Dev (no confirmation)
./scripts/db/full-reset.sh prod  # Prod (requires typing confirmation)
```

**What it does:**

1. Drops all tables
2. **DELETES all migration files**
3. **Regenerates migrations** from current models
4. Applies new migrations
5. Clears Supabase, creates admin, seeds data

**Warning:** This regenerates migration files with new hashes. If you commit these, they'll conflict with any other branch that also ran full-reset.

---

### 4. `seed/all.sh` - Just Seed Data

**Use when:** Database schema is fine, you just need seed data.

```bash
./scripts/seed/all.sh dev
./scripts/seed/all.sh prod
```

**Current seeders:**

- Beginner swimming program
- Discount codes
- Content posts
- Announcements

**Adding a new seeder:**

1. Create your script in `scripts/seed/`
2. Add JSON data to `scripts/seed-data/` (if needed)
3. Add entry to `SEED_TASKS` in `seed/all.sh`

---

## Typical Development Workflows

### Starting a new feature (no model changes)

```bash
# 1. Reset to clean state
./scripts/db/reset.sh dev

# 2. Work on your feature
# 3. Commit your changes
```

### Adding a database field

```bash
# 1. Modify model in services/<service>/models.py
# 2. Generate migration
./scripts/db/migrate.sh members_service "add profile_picture_url"

# 3. Review the generated file in services/members_service/alembic/versions/

# 4. Test it
./scripts/db/reset.sh dev

# 5. Commit both the model change AND the migration file
git add services/members_service/models.py
git add services/members_service/alembic/versions/*.py
git commit -m "feat(members): add profile picture URL field"
```

### Creating a new table

```bash
# 1. Add model class to services/<service>/models.py
# 2. Generate migration
./scripts/db/migrate.sh payments_service "add invoices table"

# 3. Review, test, commit
```

### Switching between branches with different schemas

```bash
# If branches have compatible migrations
./scripts/db/reset.sh dev

# If migrations conflict (rare, usually means coordination issue)
./scripts/db/full-reset.sh dev  # Nuclear - regenerates everything
```

---

## Environment Files

Scripts accept environment as first argument:

- `dev` → `.env.dev`
- `prod` → `.env.prod`
- Custom path → `./path/to/.env.custom`

Default is `.env.dev` for `db/reset.sh` and `db/migrate.sh`.
Default is `.env.prod` for `db/full-reset.sh` (with confirmation prompt).

---

## Troubleshooting

### "Migration files differ between runs"

This happens when you run `db/full-reset.sh` - it regenerates migrations with new IDs.

**Solution:** Only use `db/full-reset.sh` when absolutely necessary. For daily work, use `db/reset.sh`.

### "alembic.util.exc.CommandError: Can't locate revision"

The database's migration history doesn't match available migration files.

**Solution:**

```bash
./scripts/db/reset.sh dev  # Usually fixes it
# If not:
./scripts/db/full-reset.sh dev  # Nuclear option
```

### "No changes in schema detected"

When running `db/migrate.sh`, this means your model changes haven't been detected.

**Check:**

1. Did you save the models.py file?
2. Is the model imported in the alembic env.py?
3. Are you modifying the right service?

---

## Notes

All Python scripts should be run from the project root directory to ensure proper module imports work correctly.
