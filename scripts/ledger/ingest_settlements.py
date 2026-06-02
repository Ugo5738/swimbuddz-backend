"""Ingest Paystack settlements and post the clearing-drain entry for each
(DR bank_operating_ngn + DR expense_psp_fees / CR paystack_clearing) — closes
``paystack_clearing`` to the bank (design §11, R3).

Dry-run by default: fetches + reports what WOULD drain, with NO DB writes and NO
ledger posts. Pass --commit to persist. Idempotent (settlements deduped by
Paystack id; ledger entries deduped by key payments:settlement:<id>).

Must run via the PAYMENTS image/container (it carries PAYSTACK_SECRET_KEY + the
settlement-ingest code + the ledger client config):
  docker exec -w /app swimbuddz_payments \
    python scripts/ledger/ingest_settlements.py                 # dry-run
  docker exec -w /app swimbuddz_payments \
    python scripts/ledger/ingest_settlements.py --commit        # persist + post
  docker exec -w /app swimbuddz_payments \
    python scripts/ledger/ingest_settlements.py --lookback-days 400  # drain history
"""

from __future__ import annotations

import argparse
import asyncio

from services.payments_service.tasks import ingest_paystack_settlements


async def main(commit: bool, lookback_days: int) -> None:
    print(f"lookback_days={lookback_days}  mode={'COMMIT' if commit else 'DRY-RUN'}\n")
    summary = await ingest_paystack_settlements(
        lookback_days=lookback_days, commit=commit
    )
    if summary.get("error"):
        raise SystemExit(f"FETCH FAILED: {summary['error']}")

    print(f"settlements fetched:       {summary['fetched']}")
    print(f"already drained (skipped): {summary['skipped_posted']}")
    if commit:
        print(f"newly recorded:            {summary['new']}")
        print(f"drain entries posted:      {summary['posted']}")
        print(f"dead-lettered (failed):    {summary['failed']}")
        print("\nCOMMITTED.")
    else:
        print(f"would record (new):        {summary['new']}")
        print(
            f"would drain (gross):       "
            f"NGN {summary['would_drain_minor'] / 100:,.2f}"
        )
        print(
            "\nDRY-RUN — no writes, no ledger posts. Re-run with --commit to persist."
        )


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Ingest Paystack settlements -> ledger.")
    ap.add_argument(
        "--commit", action="store_true", help="Persist + post (default: dry-run)."
    )
    ap.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="Days back to fetch settlements (default 30).",
    )
    args = ap.parse_args()
    asyncio.run(main(args.commit, args.lookback_days))
