"""One-off backfill: post historical PAID payments into the ledger.

Why: payments marked PAID *before* the ledger emitter existed never posted, so
the ledger (and the finance reports) are empty of historical revenue. This
replays each PAID payment through the SAME emitter mapping
(`services.payments_service.services.ledger_emit.build_post_kwargs`), dated by
the payment's real `paid_at` so monthly P&L is accurate (back-dated periods
auto-create on first post).

Safety: the ledger derives idempotency from
`source_service:source_type:source_id` (= `payments:payment_paid:<reference>`),
so this is idempotent — re-runnable, and a future live emit of the same payment
will NOT double-post.

Dry-run by default; pass --commit to actually post.

Must run inside the compose network (to reach ledger-service), via the GATEWAY
image (it carries the payments code + ledger_client). The prod image may predate
this file, so mount it:

  docker compose -f docker-compose.prod.yml run --rm -T --no-deps \
    -v /home/deploy/swimbuddz-backend/backfill_payments.py:/app/bf.py \
    gateway python /app/bf.py            # dry-run
  ...                                    gateway python /app/bf.py --commit  # post
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from libs.common.config import get_settings
from libs.common.ledger_client import post_journal_entry
from services.payments_service.models import Payment, PaymentStatus
from services.payments_service.services.ledger_emit import build_post_kwargs, to_kobo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker


async def main(commit: bool, limit: int | None) -> None:
    settings = get_settings()
    engine = create_async_engine(
        settings.DATABASE_URL, connect_args={"prepare_threshold": 0}
    )
    factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    count_by_purpose: Counter[str] = Counter()
    kobo_by_purpose: Counter[str] = Counter()
    skipped: list[str] = []
    posted = 0
    failed = 0

    try:
        async with factory() as session:
            stmt = (
                select(Payment)
                .where(Payment.status == PaymentStatus.PAID)
                .order_by(Payment.paid_at)
            )
            if limit:
                stmt = stmt.limit(limit)
            payments = (await session.execute(stmt)).scalars().all()

            print(f"PAID payments to process: {len(payments)}")
            print(f"mode: {'COMMIT (posting)' if commit else 'DRY-RUN (no writes)'}\n")

            for p in payments:
                if not p.amount:
                    # amount 0/None == a 100%-discount comp (provider "discount").
                    # No cash moved, so there is nothing to post to a cash ledger
                    # (the ledger rejects zero/zero lines as degenerate anyway).
                    skipped.append(f"{p.reference} (zero amount — comp/discount)")
                    continue
                kwargs = build_post_kwargs(p)
                if kwargs is None:
                    skipped.append(f"{p.reference} (unmapped purpose={p.purpose})")
                    continue
                purpose = p.purpose.value
                count_by_purpose[purpose] += 1
                kobo_by_purpose[purpose] += to_kobo(p.amount)
                if commit:
                    try:
                        await post_journal_entry(calling_service="payments", **kwargs)
                        posted += 1
                    except Exception as exc:  # noqa: BLE001 — report, keep going
                        failed += 1
                        print(f"  FAIL {p.reference}: {str(exc)[:140]}")

        print("\n================= SUMMARY =================")
        mapped = sum(count_by_purpose.values())
        total_kobo = sum(kobo_by_purpose.values())
        print(f"mapped:                     {mapped}")
        print(f"skipped (unmapped / null):  {len(skipped)}")
        for s in skipped:
            print(f"    - {s}")
        print("\nby purpose (count | NGN):")
        for purpose, n in count_by_purpose.most_common():
            print(f"    {purpose:28} {n:4}   NGN {kobo_by_purpose[purpose] / 100:,.2f}")
        print(
            f"\nGRAND TOTAL cash-in: NGN {total_kobo / 100:,.2f}  ({total_kobo} kobo)"
        )
        if commit:
            print(f"\nposted: {posted}   failed: {failed}")
            if failed:
                print("Failed entries are idempotent — just re-run to retry them.")
        else:
            print("\nDRY-RUN — nothing written. Re-run with --commit to post.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backfill PAID payments into the ledger.")
    ap.add_argument(
        "--commit", action="store_true", help="Actually post (default: dry-run)."
    )
    ap.add_argument(
        "--limit", type=int, default=None, help="Process at most N payments (testing)."
    )
    args = ap.parse_args()
    asyncio.run(main(args.commit, args.limit))
