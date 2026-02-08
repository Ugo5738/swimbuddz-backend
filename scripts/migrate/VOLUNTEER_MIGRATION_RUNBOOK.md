# Volunteer Service Migration Runbook

Migrating volunteer functionality from `members_service` to the new
dedicated `volunteer_service` (port 8012).

## Overview

| Step | Action | Reversible? | Risk |
|------|--------|-------------|------|
| 1 | Rename old tables → legacy_ prefix | YES (alembic downgrade) | None |
| 2 | Deploy volunteer_service (creates new tables) | YES | None |
| 3 | Run data migration script | YES (idempotent, legacy untouched) | None |
| 4 | Verify migration | Read-only | None |
| 5 | Drop legacy tables | **NO** | Data loss if step 4 failed |

**Key safety principle:** Legacy tables are NEVER modified or deleted until
step 5, and step 5 requires explicit `--confirm` flag after verification.

---

## Dev Environment

Dev can be reset entirely. No migration needed.

```bash
cd swimbuddz-backend

# Option A: Full reset (simplest — nukes everything and rebuilds)
./scripts/db/full-reset.sh dev

# Option B: Incremental (if you want to test the migration flow)
./scripts/db/reset.sh dev
# The reset script runs all service migrations including volunteer_service
# and seeds default volunteer roles
```

---

## Production Environment

### Pre-flight Checklist

- [ ] Database backup taken (Supabase dashboard → Backups, or `pg_dump`)
- [ ] All code deployed (members_service changes + volunteer_service)
- [ ] Volunteer_service health check passing: `curl https://api.swimbuddz.com/api/v1/volunteers/health`

### Step 1: Apply members_service migration (rename tables)

This renames `volunteer_roles` → `legacy_volunteer_roles` and
`volunteer_interests` → `legacy_volunteer_interests`. All data preserved.

```bash
# From the swimbuddz-backend directory
cd swimbuddz-backend

# Apply the migration
alembic -c services/members_service/alembic.ini upgrade head

# Verify: legacy tables should exist
# (run from psql or Supabase SQL editor)
# SELECT COUNT(*) FROM legacy_volunteer_roles;
# SELECT COUNT(*) FROM legacy_volunteer_interests;
```

**If something goes wrong:**
```bash
# Rollback: restores original table names
alembic -c services/members_service/alembic.ini downgrade -1
```

### Step 2: Apply volunteer_service migration (create new tables)

```bash
alembic -c services/volunteer_service/alembic.ini upgrade head

# Verify: new empty tables should exist
# SELECT COUNT(*) FROM volunteer_roles;      -- should be 0
# SELECT COUNT(*) FROM volunteer_profiles;   -- should be 0
```

### Step 3: Seed default roles

```bash
python scripts/seed/volunteers.py
# Should create 13 default volunteer roles
```

### Step 4: Migrate data from legacy tables

```bash
# DRY RUN first — see what will happen, no changes made
python scripts/migrate/volunteer_data.py --dry-run --env prod

# If the dry run looks correct, run for real
python scripts/migrate/volunteer_data.py --env prod
```

**What this does:**
- Reads `legacy_volunteer_roles` → creates matching rows in `volunteer_roles`
  (preserving UUIDs, mapping old category strings to new enum values)
- Reads `legacy_volunteer_interests` → creates `volunteer_profiles` for each
  unique member (grouped interests → preferred_roles array)
- NEVER touches legacy tables
- Safe to re-run (skips existing records)

### Step 5: Verify migration

```bash
python scripts/migrate/volunteer_data.py --verify --env prod
```

**Expected output:**
```
VERIFICATION REPORT
  Legacy roles:     N
  New roles:        N (+ 13 seeded = N+13 or more)
  Legacy interests: M (X unique members)
  New profiles:     X

  OK: All legacy role titles exist in new table
  OK: All interested members have volunteer profiles

  RESULT: Migration verified successfully!
  SAFE TO: Drop legacy tables when ready.
```

**If verification fails:** Re-run step 4. The script is idempotent.

### Step 6: Drop legacy tables (ONLY after verified!)

Wait at least 24-48 hours after step 5 to confirm everything works in
production. The legacy tables take almost no space — there's no rush.

```bash
# Preview (shows row counts, doesn't drop anything)
python scripts/migrate/drop_legacy_volunteer_tables.py --env prod

# Actually drop (requires --confirm flag)
python scripts/migrate/drop_legacy_volunteer_tables.py --env prod --confirm
```

### Step 7: Clean up members_service code (optional, next deploy)

After legacy tables are dropped, you can remove:
- `VolunteerRole` and `VolunteerInterest` models from `members_service/models.py`
- `VolunteerRole` and `VolunteerInterest` imports from `members_service/alembic/env.py`
- `volunteer_router.py` (keep `challenge_router` which is still active)
- `volunteer_schemas.py` (keep challenge schemas)

---

## Rollback Plan

### If something goes wrong BEFORE step 6:

Legacy tables still exist. You can:

1. Revert the gateway proxy change (point `/api/v1/volunteers/*` back to members_client)
2. Revert members_service main.py (re-add volunteer_router)
3. Downgrade members_service migration: `alembic -c services/members_service/alembic.ini downgrade -1`

This restores the original table names and the old volunteer system works again.

### If something goes wrong AFTER step 6:

Legacy tables are gone. You would need to restore from the database backup
taken in the pre-flight checklist. This is why step 6 should only happen
after thorough verification and a waiting period.

---

## Category Mapping Reference

**Important:** `volunteer_service` stores enum **names** in Postgres (e.g. `RIDE_SHARE`),
not the lowercase `.value` strings (e.g. `ride_share`). Migration scripts must write
the enum names.

| Old category (string) | New category (enum name) |
|-----------------------|---------------------|
| `media` | `MEDIA` |
| `logistics` | `EVENTS_LOGISTICS` |
| `event_logistics` | `EVENTS_LOGISTICS` |
| `admin` | `OTHER` |
| `coaching_support` | `SESSION_LEAD` |
| `lane_marshal` | `LANE_MARSHAL` |
| `peer_mentor` | `MENTOR` |
| `social_ambassador` | `WELCOME` |
| *(anything else)* | `OTHER` |
