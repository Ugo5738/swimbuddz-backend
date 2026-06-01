"""Seed the SwimBuddz organization + chart of accounts into the ledger.

Idempotent. Run once per environment, in-container (recommended) or on host:

    docker compose exec ledger-service python scripts/seed/ledger_org.py

Creates the SwimBuddz Organization, seeds the sports_club chart of accounts, and
ensures an owner LedgerUser (by ADMIN_EMAIL). Prints the org UUID to set as
LEDGER_DEFAULT_ORG_ID in the environment so emitters and the org-context
dependency resolve to it.

Bootstrapping note: if a non-BYPASSRLS role is ever used (see the pre-B2B infra
task), set LEDGER_DEFAULT_ORG_ID *first* and re-run — the script sets
app.current_org_id to that id before inserting so RLS WITH CHECK passes. Under
the current `postgres` (bypassrls) role this is a no-op but harmless.
"""

from __future__ import annotations

import asyncio
import uuid

from libs.common.config import get_settings
from services.ledger_service.models import LedgerUser, Organization
from services.ledger_service.models.enums import LedgerRole
from services.ledger_service.services.accounts import seed_chart_of_accounts
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

SWIMBUDDZ_ORG_NAME = "SwimBuddz"
VERTICAL = "sports_club"


async def _set_org_context(session: AsyncSession, org_id: uuid.UUID) -> None:
    # Session-scoped so it persists across the seed's statements/commit.
    await session.execute(
        text("SELECT set_config('app.current_org_id', :org, false)"),
        {"org": str(org_id)},
    )


async def main() -> None:
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL, connect_args={"prepare_threshold": 0}
    )
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            configured = (settings.LEDGER_DEFAULT_ORG_ID or "").strip()

            org: Organization | None = None
            if configured:
                org_id = uuid.UUID(configured)
                await _set_org_context(session, org_id)
                org = await session.get(Organization, org_id)
            else:
                org_id = uuid.uuid4()
                await _set_org_context(session, org_id)
                org = (
                    await session.execute(
                        select(Organization).where(
                            Organization.name == SWIMBUDDZ_ORG_NAME
                        )
                    )
                ).scalar_one_or_none()

            if org is None:
                org = Organization(
                    id=org_id,
                    name=SWIMBUDDZ_ORG_NAME,
                    legal_name="SwimBuddz",
                    base_currency="NGN",
                    tax_country="NG",
                )
                session.add(org)
                await session.flush()
                print(f"Created organization {org.id}")
            else:
                await _set_org_context(session, org.id)
                print(f"Found existing organization {org.id}")

            counts = await seed_chart_of_accounts(session, org.id, VERTICAL)
            print(f"Chart of accounts: {counts}")

            owner_email = settings.ADMIN_EMAIL
            existing_owner = (
                await session.execute(
                    select(LedgerUser).where(
                        LedgerUser.org_id == org.id,
                        LedgerUser.email == owner_email,
                    )
                )
            ).scalar_one_or_none()
            if existing_owner is None:
                session.add(
                    LedgerUser(org_id=org.id, email=owner_email, role=LedgerRole.OWNER)
                )
                print(f"Created owner LedgerUser for {owner_email}")
            else:
                print(f"Owner LedgerUser already exists for {owner_email}")

            await session.commit()

            print("\n✅ Ledger org seed complete.")
            print(f"   Set LEDGER_DEFAULT_ORG_ID={org.id} in your environment.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
