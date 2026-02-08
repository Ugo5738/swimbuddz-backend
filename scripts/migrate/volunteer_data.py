#!/usr/bin/env python3
"""
Migrate volunteer data from legacy members_service tables to the new
volunteer_service tables.

SAFETY:
  - This script is READ from legacy tables, WRITE to new tables.
  - It NEVER deletes or modifies legacy data.
  - It skips rows that already exist in the new tables (idempotent).
  - Run it as many times as you need — it's safe to re-run.

PREREQUISITES:
  1. members_service migration applied (legacy_volunteer_roles exists)
  2. volunteer_service migration applied (volunteer_roles exists)

USAGE:
  # Dry run (shows what would be migrated, writes nothing):
  python scripts/migrate/volunteer_data.py --dry-run

  # Actually migrate:
  python scripts/migrate/volunteer_data.py

  # Use prod env:
  python scripts/migrate/volunteer_data.py --env prod

AFTER MIGRATION:
  1. Verify data: python scripts/migrate/volunteer_data.py --verify
  2. Only after verification, create a members_service migration to DROP
     legacy_volunteer_roles and legacy_volunteer_interests tables.
"""

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


# Category mapping: old string categories → new enum *names* stored in Postgres.
# NOTE: volunteer_service uses SQLAlchemy Enum members, so the DB stores names
# like "RIDE_SHARE" (not the .value strings like "ride_share").
CATEGORY_MAP = {
    "media": "MEDIA",
    "logistics": "EVENTS_LOGISTICS",
    "event_logistics": "EVENTS_LOGISTICS",
    "admin": "OTHER",
    "coaching_support": "SESSION_LEAD",
    "lane_marshal": "LANE_MARSHAL",
    "peer_mentor": "MENTOR",
    "social_ambassador": "WELCOME",
}
DEFAULT_CATEGORY = "OTHER"


async def run_migration(dry_run: bool = False, env: str = "dev"):
    """Migrate data from legacy tables to new volunteer_service tables."""

    # Load environment
    env_file = f".env.{env}" if env in ("dev", "prod") else env
    env_path = PROJECT_ROOT / env_file
    if env_path.exists():
        import dotenv

        dotenv.load_dotenv(env_path)
        print(f"Loaded environment: {env_file}")

    from libs.common.config import get_settings
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # ---------------------------------------------------------------
        # Step 1: Check that legacy tables exist
        # ---------------------------------------------------------------
        try:
            legacy_roles = await session.execute(
                text("SELECT * FROM legacy_volunteer_roles")
            )
            legacy_roles_rows = legacy_roles.fetchall()
            legacy_roles_columns = legacy_roles.keys()
        except Exception as e:
            print(f"ERROR: Cannot read legacy_volunteer_roles: {e}")
            print("Make sure the members_service rename migration has been applied.")
            return

        try:
            legacy_interests = await session.execute(
                text("SELECT * FROM legacy_volunteer_interests")
            )
            legacy_interests_rows = legacy_interests.fetchall()
            legacy_interests_columns = legacy_interests.keys()
        except Exception as e:
            print(f"ERROR: Cannot read legacy_volunteer_interests: {e}")
            print("Make sure the members_service rename migration has been applied.")
            return

        # ---------------------------------------------------------------
        # Step 2: Check that new tables exist
        # ---------------------------------------------------------------
        try:
            existing_roles = await session.execute(
                text("SELECT id, title FROM volunteer_roles")
            )
            existing_role_titles = {row[1] for row in existing_roles.fetchall()}
            existing_role_ids = set()
            existing_roles2 = await session.execute(
                text("SELECT id FROM volunteer_roles")
            )
            existing_role_ids = {row[0] for row in existing_roles2.fetchall()}
        except Exception as e:
            print(f"ERROR: Cannot read volunteer_roles: {e}")
            print("Make sure the volunteer_service initial migration has been applied.")
            return

        try:
            existing_profiles = await session.execute(
                text("SELECT member_id FROM volunteer_profiles")
            )
            existing_profile_member_ids = {
                row[0] for row in existing_profiles.fetchall()
            }
        except Exception as e:
            print(f"ERROR: Cannot read volunteer_profiles: {e}")
            return

        # ---------------------------------------------------------------
        # Step 3: Migrate roles
        # ---------------------------------------------------------------
        print(f"\n{'=' * 60}")
        print(f"LEGACY VOLUNTEER ROLES: {len(legacy_roles_rows)} found")
        print(f"{'=' * 60}")

        roles_created = 0
        roles_skipped = 0
        role_id_map = {}  # old_id → new_id (for interest migration)

        for row in legacy_roles_rows:
            row_dict = dict(zip(legacy_roles_columns, row))
            old_id = row_dict["id"]
            title = row_dict["title"]
            old_category = row_dict.get("category", "other")

            # Map old category string to new enum value
            new_category = CATEGORY_MAP.get(old_category, DEFAULT_CATEGORY)

            # Check if already migrated (by title or ID)
            if old_id in existing_role_ids:
                print(f"  SKIP (ID exists): {title}")
                role_id_map[old_id] = old_id
                roles_skipped += 1
                continue

            if title in existing_role_titles:
                print(f"  SKIP (title exists): {title}")
                # Find the existing role's ID for the interest mapping
                existing_id_result = await session.execute(
                    text("SELECT id FROM volunteer_roles WHERE title = :title"),
                    {"title": title},
                )
                existing_id = existing_id_result.scalar_one_or_none()
                role_id_map[old_id] = existing_id or old_id
                roles_skipped += 1
                continue

            print(
                f"  {'[DRY RUN] Would create' if dry_run else 'Creating'}: "
                f"{title} (category: {old_category} → {new_category})"
            )

            if not dry_run:
                now = datetime.now(timezone.utc)
                await session.execute(
                    text(
                        """
                        INSERT INTO volunteer_roles
                            (id, title, description, category,
                             required_skills, min_tier, icon,
                             is_active, sort_order,
                             created_at, updated_at)
                        VALUES
                            (:id, :title, :description, :category,
                             :required_skills, :min_tier, :icon,
                             :is_active, :sort_order,
                             :created_at, :updated_at)
                    """
                    ),
                    {
                        "id": old_id,  # Preserve original UUID!
                        "title": title,
                        "description": row_dict.get("description"),
                        "category": new_category,
                        "required_skills": None,
                        "min_tier": "TIER_1",
                        "icon": None,
                        "is_active": row_dict.get("is_active", True),
                        "sort_order": 0,
                        "created_at": row_dict.get("created_at", now),
                        "updated_at": row_dict.get("updated_at", now),
                    },
                )

            role_id_map[old_id] = old_id
            roles_created += 1

        # ---------------------------------------------------------------
        # Step 4: Migrate interests → volunteer profiles
        # ---------------------------------------------------------------
        print(f"\n{'=' * 60}")
        print(f"LEGACY VOLUNTEER INTERESTS: {len(legacy_interests_rows)} found")
        print(f"{'=' * 60}")

        # Group interests by member_id (one profile per member)
        member_interests: dict[uuid.UUID, list[dict]] = {}
        for row in legacy_interests_rows:
            row_dict = dict(zip(legacy_interests_columns, row))
            member_id = row_dict["member_id"]
            if member_id not in member_interests:
                member_interests[member_id] = []
            member_interests[member_id].append(row_dict)

        profiles_created = 0
        profiles_skipped = 0

        for member_id, interests in member_interests.items():
            if member_id in existing_profile_member_ids:
                print(f"  SKIP (profile exists): member {member_id}")
                profiles_skipped += 1
                continue

            # Collect preferred roles from all their interests
            preferred_role_categories = []
            for interest in interests:
                old_role_id = interest["role_id"]
                new_role_id = role_id_map.get(old_role_id)
                if new_role_id:
                    preferred_role_categories.append(str(new_role_id))

            # Combine notes from all interests
            notes_parts = [i.get("notes") for i in interests if i.get("notes")]
            combined_notes = "; ".join(notes_parts) if notes_parts else None

            print(
                f"  {'[DRY RUN] Would create' if dry_run else 'Creating'} profile: "
                f"member {member_id} ({len(interests)} interest(s))"
            )

            if not dry_run:
                now = datetime.now(timezone.utc)
                await session.execute(
                    text(
                        """
                        INSERT INTO volunteer_profiles
                            (id, member_id, tier, total_hours,
                             total_sessions_volunteered, total_no_shows,
                             total_late_cancellations, reliability_score,
                             preferred_roles, notes, is_active,
                             created_at, updated_at)
                        VALUES
                            (:id, :member_id, 'TIER_1', 0.0,
                             0, 0,
                             0, 100,
                             :preferred_roles, :notes, true,
                             :created_at, :updated_at)
                    """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "member_id": member_id,
                        "preferred_roles": preferred_role_categories or None,
                        "notes": combined_notes,
                        "created_at": interests[0].get("created_at", now),
                        "updated_at": now,
                    },
                )

            profiles_created += 1

        # ---------------------------------------------------------------
        # Commit
        # ---------------------------------------------------------------
        if not dry_run:
            await session.commit()
            print("\nChanges committed to database.")
        else:
            print("\n[DRY RUN] No changes made to database.")

        # ---------------------------------------------------------------
        # Summary
        # ---------------------------------------------------------------
        print(f"\n{'=' * 60}")
        print("MIGRATION SUMMARY")
        print(f"{'=' * 60}")
        print(f"  Roles:    {roles_created} created, {roles_skipped} skipped")
        print(f"  Profiles: {profiles_created} created, {profiles_skipped} skipped")
        print(f"  Legacy tables: UNTOUCHED (safe to inspect)")
        print()

    await engine.dispose()


async def run_verify(env: str = "dev"):
    """Verify that legacy and new tables have consistent data."""
    env_file = f".env.{env}" if env in ("dev", "prod") else env
    env_path = PROJECT_ROOT / env_file
    if env_path.exists():
        import dotenv

        dotenv.load_dotenv(env_path)

    from libs.common.config import get_settings
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        # Count legacy
        legacy_roles_count = (
            await session.execute(text("SELECT COUNT(*) FROM legacy_volunteer_roles"))
        ).scalar()
        legacy_interests_count = (
            await session.execute(
                text("SELECT COUNT(*) FROM legacy_volunteer_interests")
            )
        ).scalar()

        # Count new
        new_roles_count = (
            await session.execute(text("SELECT COUNT(*) FROM volunteer_roles"))
        ).scalar()
        new_profiles_count = (
            await session.execute(text("SELECT COUNT(*) FROM volunteer_profiles"))
        ).scalar()

        # Count unique members in legacy interests
        legacy_member_count = (
            await session.execute(
                text("SELECT COUNT(DISTINCT member_id) FROM legacy_volunteer_interests")
            )
        ).scalar()

        print(f"\n{'=' * 60}")
        print("VERIFICATION REPORT")
        print(f"{'=' * 60}")
        print(f"  Legacy roles:     {legacy_roles_count}")
        print(f"  New roles:        {new_roles_count}")
        print(
            f"  Legacy interests: {legacy_interests_count} ({legacy_member_count} unique members)"
        )
        print(f"  New profiles:     {new_profiles_count}")
        print()

        # Check that all legacy roles have a corresponding new role
        legacy_titles = await session.execute(
            text("SELECT title FROM legacy_volunteer_roles")
        )
        new_titles = await session.execute(text("SELECT title FROM volunteer_roles"))
        legacy_set = {row[0] for row in legacy_titles.fetchall()}
        new_set = {row[0] for row in new_titles.fetchall()}

        missing_roles = legacy_set - new_set
        if missing_roles:
            print(f"  WARNING: {len(missing_roles)} legacy roles NOT in new table:")
            for title in missing_roles:
                print(f"    - {title}")
        else:
            print("  OK: All legacy role titles exist in new table")

        # Check that all interested members have profiles
        legacy_members = await session.execute(
            text("SELECT DISTINCT member_id FROM legacy_volunteer_interests")
        )
        new_members = await session.execute(
            text("SELECT member_id FROM volunteer_profiles")
        )
        legacy_member_set = {row[0] for row in legacy_members.fetchall()}
        new_member_set = {row[0] for row in new_members.fetchall()}

        missing_members = legacy_member_set - new_member_set
        if missing_members:
            print(f"  WARNING: {len(missing_members)} members WITHOUT profiles:")
            for mid in missing_members:
                print(f"    - {mid}")
        else:
            print("  OK: All interested members have volunteer profiles")

        all_good = not missing_roles and not missing_members
        print()
        if all_good:
            print("  RESULT: Migration verified successfully!")
            print("  SAFE TO: Drop legacy tables when ready.")
        else:
            print("  RESULT: Issues found. Re-run the migration script.")
            print("  DO NOT drop legacy tables until all issues are resolved.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate volunteer data from legacy to new tables"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify migration results (compare legacy vs new)",
    )
    parser.add_argument(
        "--env",
        default="dev",
        help="Environment: dev, prod, or path to env file (default: dev)",
    )

    args = parser.parse_args()

    if args.verify:
        asyncio.run(run_verify(args.env))
    else:
        asyncio.run(run_migration(dry_run=args.dry_run, env=args.env))
