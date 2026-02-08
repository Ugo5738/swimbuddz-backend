#!/usr/bin/env python3
"""
DROP legacy volunteer tables from the database.

!!! DANGER: This is IRREVERSIBLE. Only run AFTER confirming the data
migration was successful. !!!

PREREQUISITES:
  1. Run: python scripts/migrate/volunteer_data.py --verify --env prod
  2. Confirm the output says "Migration verified successfully!"
  3. Take a database backup BEFORE running this script

USAGE:
  # This script requires explicit confirmation:
  python scripts/migrate/drop_legacy_volunteer_tables.py --env prod --confirm

  # Preview only (shows what will be dropped):
  python scripts/migrate/drop_legacy_volunteer_tables.py --env prod
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))


async def drop_legacy_tables(env: str = "dev", confirm: bool = False):
    """Drop the legacy volunteer tables."""
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
        # Check what exists
        for table in ["legacy_volunteer_interests", "legacy_volunteer_roles"]:
            try:
                count = (
                    await session.execute(text(f"SELECT COUNT(*) FROM {table}"))
                ).scalar()
                print(f"  {table}: {count} rows")
            except Exception:
                print(f"  {table}: does not exist (already dropped?)")

        if not confirm:
            print()
            print("DRY RUN: No tables dropped.")
            print("Add --confirm to actually drop the tables.")
            print()
            print("BEFORE running with --confirm:")
            print(
                "  1. Verify: python scripts/migrate/volunteer_data.py --verify --env prod"
            )
            print("  2. Backup your database")
            return

        print()
        print("DROPPING legacy tables...")

        # Drop interests first (it references roles via role_id)
        await session.execute(text("DROP TABLE IF EXISTS legacy_volunteer_interests"))
        print("  Dropped: legacy_volunteer_interests")

        await session.execute(text("DROP TABLE IF EXISTS legacy_volunteer_roles"))
        print("  Dropped: legacy_volunteer_roles")

        await session.commit()
        print()
        print("Done. Legacy tables removed.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Drop legacy volunteer tables (IRREVERSIBLE)"
    )
    parser.add_argument(
        "--env",
        default="dev",
        help="Environment: dev, prod, or path to env file",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Actually drop the tables (without this flag, it's a dry run)",
    )
    args = parser.parse_args()
    asyncio.run(drop_legacy_tables(env=args.env, confirm=args.confirm))
