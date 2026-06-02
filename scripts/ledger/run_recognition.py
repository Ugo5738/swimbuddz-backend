"""Backfill recognition schedules + run due revenue recognition (design §10).

Dry-run by default (computes, then rolls back). Pass --commit to persist.

Must run inside the compose network, via the GATEWAY image (it carries scripts/
+ the ledger code):
  docker compose -f docker-compose.prod.yml run --rm -T --no-deps \
    -e LEDGER_DEFAULT_ORG_ID=<org> gateway python scripts/ledger/run_recognition.py
  ...                                                python ... --commit   # persist
  ...                                                python ... --as-of 2026-06-30
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import date, datetime

from libs.common.config import get_settings
from services.ledger_service.services.recognition import (
    backfill_schedules,
    run_due_recognition,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


async def main(commit: bool, as_of: date) -> None:
    settings = get_settings()
    org_raw = (settings.LEDGER_DEFAULT_ORG_ID or "").strip()
    if not org_raw:
        raise SystemExit("LEDGER_DEFAULT_ORG_ID is not set")
    org_id = uuid.UUID(org_raw)

    engine = create_async_engine(
        settings.DATABASE_URL, connect_args={"prepare_threshold": 0}
    )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    print(
        f"org={org_id}  as_of={as_of.isoformat()}  mode={'COMMIT' if commit else 'DRY-RUN'}\n"
    )
    try:
        async with factory() as session:
            # Org RLS context — no-op under the bypassrls role, correct under a
            # scoped role (so this script is forward-compatible with R7).
            await session.execute(
                text("SELECT set_config('app.current_org_id', :o, false)"),
                {"o": str(org_id)},
            )

            created = await backfill_schedules(session, org_id)
            print(f"recognition schedules backfilled (new): {created}")

            summary = await run_due_recognition(session, org_id, as_of)
            print(f"recognition run: {summary}")
            print(
                f"  recognised this run: NGN {summary['recognized_minor'] / 100:,.2f}"
            )

            if commit:
                await session.commit()
                print("\nCOMMITTED.")
            else:
                await session.rollback()
                print("\nDRY-RUN — rolled back. Re-run with --commit to persist.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Backfill schedules + run revenue recognition."
    )
    ap.add_argument(
        "--commit", action="store_true", help="Persist (default: dry-run/rollback)."
    )
    ap.add_argument(
        "--as-of", default=None, help="ISO date (YYYY-MM-DD); default today."
    )
    args = ap.parse_args()
    as_of_date = (
        datetime.strptime(args.as_of, "%Y-%m-%d").date() if args.as_of else date.today()
    )
    asyncio.run(main(args.commit, as_of_date))
