"""One-time: book the promo-Bubbles liability that exists BEFORE the wallet
ledger emitter goes live.

The ledger has no prior wallet history, so unexpired promotional grants represent
a promo liability that was never booked. Without this, the first post-launch
spends that draw on those grants would debit `bubbles_liability_promo` with no
matching credit and push it negative. This posts a single opening entry:

    DR expense_marketing / CR bubbles_liability_promo = SUM(active grants'
    bubbles_remaining) x NAIRA_PER_BUBBLE

Idempotent (fixed source id -> ledger idempotency key). Dry-run by default.

Run once, after the wallet emitter deploys, via the gateway image:
  docker compose -f docker-compose.prod.yml run --rm -T --no-deps \
    -e LEDGER_DEFAULT_ORG_ID=<org> gateway \
    python scripts/ledger/wallet_promo_opening.py            # dry-run
  ...                                                        python ... --commit
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import date

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.ledger_client import post_journal_entry
from services.wallet_service.models import PromotionalBubbleGrant
from services.wallet_service.services.ledger_emit import NAIRA_PER_BUBBLE
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


async def main(commit: bool) -> None:
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL, connect_args={"prepare_threshold": 0}
    )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            now = utc_now()
            total_bubbles = (
                await session.execute(
                    select(
                        func.coalesce(
                            func.sum(PromotionalBubbleGrant.bubbles_remaining), 0
                        )
                    ).where(
                        PromotionalBubbleGrant.bubbles_remaining > 0,
                        or_(
                            PromotionalBubbleGrant.expires_at.is_(None),
                            PromotionalBubbleGrant.expires_at > now,
                        ),
                    )
                )
            ).scalar() or 0

        kobo = int(total_bubbles) * NAIRA_PER_BUBBLE
        print(
            f"active promo Bubbles outstanding: {total_bubbles} -> NGN {kobo / 100:,.2f}"
        )
        if kobo <= 0:
            print("nothing to open. done.")
            return
        if not commit:
            print(
                "DRY-RUN — would post DR expense_marketing / CR "
                "bubbles_liability_promo. Re-run with --commit."
            )
            return

        await post_journal_entry(
            calling_service="wallet",
            entry_date=date.today().isoformat(),
            description="Wallet promo-Bubbles opening balance",
            source_service="wallet",
            source_type="promo_opening",
            source_id="v1",
            org_id=settings.LEDGER_DEFAULT_ORG_ID or None,
            metadata={"outstanding_bubbles": int(total_bubbles)},
            lines=[
                {
                    "account_ref": "expense_marketing",
                    "debit": kobo,
                    "credit": 0,
                    "currency": "NGN",
                },
                {
                    "account_ref": "bubbles_liability_promo",
                    "debit": 0,
                    "credit": kobo,
                    "currency": "NGN",
                },
            ],
        )
        print(f"POSTED opening entry: CR bubbles_liability_promo NGN {kobo / 100:,.2f}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="Book the wallet promo-Bubbles opening balance."
    )
    ap.add_argument("--commit", action="store_true", help="Persist (default: dry-run).")
    args = ap.parse_args()
    asyncio.run(main(args.commit))
