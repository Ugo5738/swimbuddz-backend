"""Seed a test cohort for an existing academy program.

Companion to ``seed/program.py``. The program seeder ships
``is_published=false`` (so the seeded program won't show up on the public
academy page or in ``/cohorts/enrollable``) and creates no cohorts —
which means the academy registration flow has nothing to enrol against
out of the box. This script fills that gap:

  1. Resolves a program by slug (default: ``beginner-freestyle-50m`` —
     the slug used by ``seed-data/freestyle_beginner.json``).
  2. If the program is unpublished, publishes it (so the cohort shows up
     in ``/api/v1/academy/cohorts/enrollable``). Disable with
     ``--no-publish`` if you want to test the "program hidden" path.
  3. Creates a new OPEN cohort with sensible defaults — name auto-built
     from today's month/year, start_date a week out, end_date based on
     ``program.duration_weeks``, capacity from ``program.default_capacity``.

Idempotency: re-running creates ANOTHER cohort each time. Pass ``--name``
to control naming, or use the dedicated cleanup query in the printed
output if you need to tidy up.

Usage:
  cd swimbuddz-backend

  # Defaults — picks up the freestyle_beginner seed program, makes
  # one OPEN cohort starting next week.
  ENV_FILE=.env.dev python scripts/seed/cohort.py

  # Custom program / name / dates
  ENV_FILE=.env.dev python scripts/seed/cohort.py \\
      --program-slug beginner-freestyle-50m \\
      --name "Smoke Test May 2026" \\
      --start-date 2026-05-24 \\
      --capacity 5

  # Test "program is hidden" path — create cohort without auto-publishing
  ENV_FILE=.env.dev python scripts/seed/cohort.py --no-publish
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

# Add backend root to path so we can import services + libs (3 levels up:
# seed → scripts → backend root).
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from dotenv import load_dotenv

# Load env BEFORE importing libs that call get_settings(). Default to .env.dev
# since this is a test-data utility.
project_root = Path(__file__).resolve().parents[2]
env_file = os.environ.get("ENV_FILE", ".env.dev")
load_dotenv(project_root / env_file, override=True)

from libs.db.config import AsyncSessionLocal
from services.academy_service.models import (
    Cohort,
    CohortStatus,
    CohortType,
    LocationType,
    Program,
)
from sqlalchemy import select


def _default_cohort_name() -> str:
    """e.g. 'Smoke Test May 2026' — month-year stamp keeps repeated runs
    sortable in the admin UI without manual --name flags."""
    today = datetime.now(timezone.utc)
    return f"Smoke Test {today.strftime('%b %Y')}"


def _parse_date(s: str) -> datetime:
    """Parse YYYY-MM-DD into a UTC midnight datetime. The cohort columns
    are timezone-aware datetimes, so we attach UTC explicitly."""
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


async def seed_cohort(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as session:
        # 1. Find the program by slug.
        result = await session.execute(
            select(Program).where(Program.slug == args.program_slug)
        )
        program = result.scalar_one_or_none()
        if not program:
            print(
                f"❌ Program with slug {args.program_slug!r} not found.\n"
                f"   Seed it first:\n"
                f"     python scripts/seed/program.py "
                f"--file scripts/seed-data/freestyle_beginner.json"
            )
            return 1

        print(f"✓ Found program: {program.name} (id={program.id}, slug={program.slug})")
        print(f"  duration_weeks={program.duration_weeks}  "
              f"default_capacity={program.default_capacity}  "
              f"is_published={program.is_published}")

        # 2. Optionally publish the program so the cohort appears in
        #    /api/v1/academy/cohorts/enrollable (which filters on
        #    Program.is_published.is_(True)).
        if not program.is_published and not args.no_publish:
            print("  · Program was unpublished — publishing it so the cohort "
                  "appears as enrollable.")
            program.is_published = True
        elif not program.is_published:
            print("  · Program left unpublished (--no-publish). Cohort will NOT "
                  "appear in /cohorts/enrollable until you publish it.")

        # 3. Build the cohort.
        start_date = _parse_date(args.start_date) if args.start_date else (
            datetime.now(timezone.utc) + timedelta(days=7)
        )
        end_date = _parse_date(args.end_date) if args.end_date else (
            start_date + timedelta(weeks=program.duration_weeks or 12)
        )
        capacity = args.capacity if args.capacity is not None else (
            program.default_capacity or 10
        )

        cohort = Cohort(
            id=uuid4(),
            program_id=program.id,
            name=args.name,
            start_date=start_date,
            end_date=end_date,
            capacity=capacity,
            type=CohortType.GROUP,
            status=CohortStatus.OPEN,
            location_type=LocationType.POOL,
            location_name=args.location_name,
            timezone=args.timezone,
        )
        session.add(cohort)
        await session.commit()
        await session.refresh(cohort)

        print(
            f"\n✅ Created cohort:\n"
            f"   id          : {cohort.id}\n"
            f"   name        : {cohort.name}\n"
            f"   program     : {program.name} ({program.slug})\n"
            f"   start_date  : {cohort.start_date.isoformat()}\n"
            f"   end_date    : {cohort.end_date.isoformat()}\n"
            f"   capacity    : {cohort.capacity}\n"
            f"   status      : {cohort.status.value}\n"
            f"   location    : {cohort.location_name or '(unset)'}\n"
        )
        print("Useful URLs for testing the registration flow:")
        print(f"   Public program page : /academy/programs/{program.slug}")
        print(f"   Member cohort page  : /account/academy/cohorts/{cohort.id}")
        print(
            f"   Enrollable API      : "
            f"/api/v1/academy/cohorts/enrollable?program_id={program.id}"
        )
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create a test cohort under an existing academy program."
    )
    parser.add_argument(
        "--program-slug",
        default="beginner-freestyle-50m",
        help="Slug of the program to attach the cohort to (default: %(default)s, "
        "the slug used by seed-data/freestyle_beginner.json).",
    )
    parser.add_argument(
        "--name",
        default=_default_cohort_name(),
        help="Cohort display name (default: 'Smoke Test <Month Year>').",
    )
    parser.add_argument(
        "--start-date",
        help="ISO date (YYYY-MM-DD). Default: 7 days from today.",
    )
    parser.add_argument(
        "--end-date",
        help="ISO date (YYYY-MM-DD). Default: start_date + program.duration_weeks.",
    )
    parser.add_argument(
        "--capacity",
        type=int,
        help="Cohort capacity (default: program.default_capacity).",
    )
    parser.add_argument(
        "--location-name",
        default="Yaba Pool",
        help="Free-text location label (default: %(default)s).",
    )
    parser.add_argument(
        "--timezone",
        default="Africa/Lagos",
        help="IANA timezone for the cohort (default: %(default)s).",
    )
    parser.add_argument(
        "--no-publish",
        action="store_true",
        help="Skip auto-publishing the program. Use to test the "
        "'program is hidden' code path.",
    )
    args = parser.parse_args()

    return asyncio.run(seed_cohort(args))


if __name__ == "__main__":
    sys.exit(main())
