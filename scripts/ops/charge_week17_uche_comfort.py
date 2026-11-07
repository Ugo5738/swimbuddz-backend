#!/usr/bin/env python3
"""One-off ops: record Uche + Comfort's Sat Jun 20 swim (Week 17, cohort final)
and charge each the 35-Bubble pool fee on-the-books.

Context (2026-06-26, Daniel): both members swam at their cohort's final class
on Sat Jun 20 but couldn't pay at the time (empty wallets). Their wallets have
since been topped up. This applies the per-session pool fee the same way a
normal member sign-in does — via debit_member_wallet → wallet-service → ledger
emit (revenue_club_session) — then records the PRESENT attendance row linked to
the wallet transaction. There is no admin UI for "charge a member for a past
session", which is why this runs as an operator script INSIDE the cluster
(so the wallet + ledger services are reachable and the journal entry posts).

It is a faithful replica of services/attendance_service/routers/member/sign_in.py:
  - same idempotency key  f"session-fee-{session_id}-{member_id}"
    => safe to re-run, and a later real sign-in will NOT double-charge.
  - transaction_type=purchase, reference_type=session, service_source=attendance
    => the ledger emitter maps (attendance, session) -> revenue_club_session.

RUN INSIDE the attendance-service container (has the attendance DB session +
service_client + models). Dry-run by default; pass --apply to write.

  # copy in + run:
  docker compose cp scripts/ops/charge_week17_uche_comfort.py \
      attendance-service:/tmp/charge.py
  docker compose exec attendance-service python /tmp/charge.py           # dry run
  docker compose exec attendance-service python /tmp/charge.py --apply   # execute

  # or pipe via stdin (no copy, no image rebuild):
  docker compose exec -T attendance-service python - --apply \
      < scripts/ops/charge_week17_uche_comfort.py
"""

import argparse
import asyncio
import sys
import uuid

from sqlalchemy import select

from libs.common.currency import kobo_to_bubbles
from libs.common.service_client import debit_member_wallet, get_session_by_id
from libs.common.service_client.wallet import get_wallet_balance
from libs.db.config import AsyncSessionLocal
from services.attendance_service.models import (
    AttendanceRecord,
    AttendanceRole,
    AttendanceStatus,
)

SESSION_ID = "8e5a6d72-f091-4f28-a500-cbacfe4190aa"  # Week 17 (Extended), Jun 20
CALLING_SERVICE = "attendance"

# (display_name, member_id, auth_id)
MEMBERS = [
    (
        "Uchechukwu Okorie",
        "2c5a1dc0-fb95-4eb8-bcc7-2aadc0d98461",
        "d56f89f0-f14c-48a9-922d-7b3aeba1a839",
    ),
    (
        "Comfort Uwaheren",
        "9c011b54-650d-462f-a5d4-b5ae2c2cdc73",
        "b230ad63-c36c-4c07-8a2e-bf131701b552",
    ),
]


async def balance(auth_id: str):
    try:
        b = await get_wallet_balance(auth_id, calling_service=CALLING_SERVICE)
        return b.get("balance") if b else None
    except Exception as e:  # noqa: BLE001
        return f"(balance lookup failed: {e})"


async def upsert_attendance(db, member_id: uuid.UUID, txn_id, title: str):
    """Mirror sign_in.py's attendance create/update (PRESENT, swimmer, linked txn)."""
    existing = (
        await db.execute(
            select(AttendanceRecord).where(
                AttendanceRecord.session_id == uuid.UUID(SESSION_ID),
                AttendanceRecord.member_id == member_id,
            )
        )
    ).scalar_one_or_none()

    note = "Manual on-books charge 2026-06-26 — cohort Week 17 drop-in (Daniel)"
    if existing:
        existing.status = AttendanceStatus.PRESENT
        existing.role = AttendanceRole.SWIMMER
        if txn_id and existing.wallet_transaction_id is None:
            existing.wallet_transaction_id = uuid.UUID(txn_id)
        if not existing.notes:
            existing.notes = note
        return existing, "updated"

    row = AttendanceRecord(
        session_id=uuid.UUID(SESSION_ID),
        member_id=member_id,
        status=AttendanceStatus.PRESENT,
        role=AttendanceRole.SWIMMER,
        wallet_transaction_id=uuid.UUID(txn_id) if txn_id else None,
        notes=note,
    )
    db.add(row)
    return row, "created"


async def main(apply: bool):
    session = await get_session_by_id(SESSION_ID, calling_service=CALLING_SERVICE)
    if not session:
        sys.exit(f"Session {SESSION_ID} not found — aborting.")
    title = session.get("title", "")
    fee_kobo = session.get("pool_fee") or 0
    fee_bubbles = kobo_to_bubbles(fee_kobo)
    print(f"Session : {title}")
    print(f"Pool fee: {fee_kobo} kobo  ->  {fee_bubbles} 🫧 per swimmer")
    if fee_bubbles != 35:
        print(f"!! WARNING: expected 35 🫧, got {fee_bubbles}. Review before --apply.")
    print(f"Mode    : {'APPLY (writing)' if apply else 'DRY RUN (no writes)'}\n")

    async with AsyncSessionLocal() as db:
        for name, member_id, auth_id in MEMBERS:
            bal_before = await balance(auth_id)
            print(f"--- {name} ---")
            print(f"  balance before: {bal_before} 🫧")

            if not apply:
                print(
                    f"  WOULD debit {fee_bubbles} 🫧 (purchase/session) + mark PRESENT\n"
                )
                continue

            idem = f"session-fee-{SESSION_ID}-{member_id}"
            try:
                txn = await debit_member_wallet(
                    auth_id,
                    amount=fee_bubbles,
                    idempotency_key=idem,
                    description=f"Session fee — {title} ({fee_bubbles} 🫧)",
                    calling_service=CALLING_SERVICE,
                    transaction_type="purchase",
                    reference_type="session",
                    reference_id=SESSION_ID,
                )
            except Exception as e:  # noqa: BLE001
                print(f"  !! DEBIT FAILED ({e}) — skipping attendance for {name}\n")
                continue

            txn_id = txn.get("transaction_id")
            print(
                f"  debited: txn={txn_id}  balance_after={txn.get('balance_after')} 🫧"
            )

            row, action = await upsert_attendance(
                db, uuid.UUID(member_id), txn_id, title
            )
            await db.commit()
            await db.refresh(row)
            print(
                f"  attendance {action}: id={row.id} status={row.status.value} "
                f"wallet_txn={row.wallet_transaction_id}\n"
            )

    print("Done." if apply else "Dry run complete — re-run with --apply to execute.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    args = p.parse_args()
    asyncio.run(main(args.apply))
