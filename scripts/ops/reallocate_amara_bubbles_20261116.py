"""Reviewed atomic maintenance, dry-run by default; never a runtime integration.

See docs/SESSION_WALLET_REALLOCATION_RUNBOOK.md. Preserves original receipts,
attendance, prices and wallet history. Ledger payloads are staged atomically
for scoped replay through ledger_service, never written into journal tables.
"""

import argparse
import asyncio
import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

AUTH = "b6eabde2-f536-4983-be31-b52ee1af2ef2"
MEMBER = "cd82def6-b569-4832-b5c7-042cb6a21173"
WALLET = "2cbf3791-2c89-4436-b733-f49271a2c77f"
OLD_BOOKING = "0686427e-bb71-458a-a5cd-c7614ac25876"
COMMUNITY_BOOKING = "c1655d07-a2da-47d9-a072-ed5f504bf464"
OLD_PAYMENT = "e71349ea-b049-4dd9-a7ba-697cc13315c0"
OLD_TXN = "86e13959-bd9d-497c-8235-48ba9c00cb59"
AWARD_TXN = "f11436af-338b-45e2-8579-f4fafab287c7"
OPERATION = "amara-community-bubbles-20261116"
REASON = (
    "Owner confirmed the 100-Bubble Volunteer of the Month award pays the "
    "3 October Community swim, not the 31 October Yaba swim. Reverse the "
    "52-Bubble Yaba settlement; restore NGN 5200 owed and settle Community "
    "with 100 Bubbles. No cash received or refunded."
)


def operation_id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"swimbuddz:{OPERATION}:{name}")


async def rows(db: AsyncSession, sql: str, **params: Any) -> list[dict]:
    return [dict(r) for r in (await db.execute(text(sql), params)).mappings()]


async def repair(
    db: AsyncSession, *, apply: bool = False, actor_label: str | None = None
) -> dict:
    if apply and not actor_label:
        raise ValueError("Apply requires an operator actor label")
    lock = " FOR UPDATE" if apply else ""
    if apply:
        for bid in sorted([OLD_BOOKING, COMMUNITY_BOOKING]):
            await db.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": f"offline-receipt:booking:{bid}"},
            )
    audit_id = operation_id("audit")
    audits = await rows(
        db, "SELECT new_value FROM wallet_audit_logs WHERE id=:id", id=audit_id
    )
    if audits:
        result = audits[0]["new_value"]
        actual = await rows(
            db,
            "SELECT wallet_transaction_id,payment_intent_id FROM session_bookings WHERE id=:id",
            id=uuid.UUID(COMMUNITY_BOOKING),
        )
        if (
            not actual
            or str(actual[0]["wallet_transaction_id"])
            != result["community_transaction_id"]
        ):
            raise ValueError("Existing correction linkage changed; review again")
        return {**result, "already_applied": True}

    # Lock commercial identities before the wallet, matching checkout ordering.
    bookings = await rows(
        db,
        "SELECT * FROM session_bookings WHERE id IN (:old,:new) ORDER BY id" + lock,
        old=uuid.UUID(OLD_BOOKING),
        new=uuid.UUID(COMMUNITY_BOOKING),
    )
    by_id = {str(r["id"]): r for r in bookings}
    if set(by_id) != {OLD_BOOKING, COMMUNITY_BOOKING}:
        raise ValueError("Reviewed bookings missing")
    old, new = by_id[OLD_BOOKING], by_id[COMMUNITY_BOOKING]
    for row, amount, session_id in [
        (old, 520000, "9c45145e-25c3-54a2-b886-18aa29489d8a"),
        (new, 1000000, "00e64c99-1fbe-45fc-82f7-88c5898d4bc1"),
    ]:
        if not (
            row["member_auth_id"] == AUTH
            and str(row["member_id"]) == MEMBER
            and str(row["session_id"]) == session_id
            and row["status"] == "confirmed"
            and row["party_size"] == 1
            and row["fee_amount_kobo"] == amount
            and row["member_fee_amount_kobo"] == amount
        ):
            raise ValueError("Booking identity/price/status changed")
    if (
        str(old["payment_intent_id"]) != OLD_PAYMENT
        or str(old["wallet_transaction_id"]) != OLD_TXN
        or new["payment_intent_id"]
        or new["wallet_transaction_id"]
    ):
        raise ValueError("Payment linkage changed")
    payments = await rows(
        db, "SELECT * FROM payments WHERE id=:id" + lock, id=uuid.UUID(OLD_PAYMENT)
    )
    if len(payments) != 1:
        raise ValueError("Original payment missing")
    payment = payments[0]
    meta = payment["metadata"] or {}
    if not (
        payment["member_auth_id"] == AUTH
        and payment["reference"] == "PAY-08475"
        and payment["purpose"] == "session_booking"
        and payment["status"] == "paid"
        and payment["amount"] == 0
        and payment["currency"] == "NGN"
        and payment["provider"] == "internal"
        and payment["provider_reference"] == "free:PAY-08475"
        and not meta.get("paystack")
        and not meta.get("provider_initialization_started_at")
        and meta.get("booking_id") == OLD_BOOKING
        and meta.get("wallet_transaction_id") == OLD_TXN
        and meta.get("bubbles_to_apply") == 52
        and meta.get("server_price", {}).get("total_kobo") == 520000
        and not meta.get("booking_reconciliation")
        and not meta.get("checkout_reconciliation")
    ):
        raise ValueError(
            "Original payment is not the reviewed zero-cash wallet settlement"
        )
    others = await rows(
        db,
        "SELECT id FROM payments WHERE id<>:id AND status IN ('paid','waived','pending','pending_review') AND (session_booking_id IN (:old,:new) OR metadata->>'booking_id' IN (:olds,:news) OR metadata->'booking_ids' ?| ARRAY[:olds,:news])",
        id=uuid.UUID(OLD_PAYMENT),
        old=uuid.UUID(OLD_BOOKING),
        new=uuid.UUID(COMMUNITY_BOOKING),
        olds=OLD_BOOKING,
        news=COMMUNITY_BOOKING,
    )
    if others:
        raise ValueError("Competing payment attempt; reconcile first")
    wallets = await rows(
        db, "SELECT * FROM wallets WHERE id=:id" + lock, id=uuid.UUID(WALLET)
    )
    if (
        len(wallets) != 1
        or wallets[0]["member_auth_id"] != AUTH
        or wallets[0]["balance"] != 56
        or wallets[0]["status"] != "active"
    ):
        raise ValueError("Reviewed wallet/balance changed")
    holds = await rows(
        db,
        "SELECT id FROM wallet_holds WHERE wallet_id=:id AND status='held' AND expires_at>now()",
        id=uuid.UUID(WALLET),
    )
    grants = await rows(
        db,
        "SELECT id FROM promotional_bubble_grants WHERE wallet_id=:id AND bubbles_remaining>0",
        id=uuid.UUID(WALLET),
    )
    if holds or grants:
        raise ValueError(
            "Active holds or promo grants require a different reviewed allocation"
        )
    txns = await rows(
        db,
        "SELECT * FROM wallet_transactions WHERE id IN (:old,:award) ORDER BY id"
        + lock,
        old=uuid.UUID(OLD_TXN),
        award=uuid.UUID(AWARD_TXN),
    )
    txn_map = {str(t["id"]): t for t in txns}
    debit, award = txn_map[OLD_TXN], txn_map[AWARD_TXN]
    if not (
        str(debit["wallet_id"]) == WALLET
        and debit["amount"] == 52
        and debit["direction"] == "debit"
        and debit["transaction_type"] == "purchase"
        and debit["status"] == "completed"
        and debit["reference_id"] == OLD_PAYMENT
        and debit["service_source"] == "payments"
        and debit["reference_type"] == "session_booking"
        and not debit["reversed_by_transaction_id"]
        and not debit["reversal_of_transaction_id"]
        and debit["txn_metadata"].get("promo_bubbles") == 0
        and debit["txn_metadata"].get("purchased_bubbles") == 52
    ):
        raise ValueError("Original wallet debit changed")
    if not (
        str(award["wallet_id"]) == WALLET
        and award["amount"] == 100
        and award["direction"] == "credit"
        and award["status"] == "completed"
        and "Oct 3 Community Swim" in award["txn_metadata"].get("reason", "")
    ):
        raise ValueError("Award evidence does not match reviewed swim")

    refund_id, purchase_id, payment_id = (
        operation_id(n) for n in ("refund", "community-purchase", "community-payment")
    )
    result = {
        "operation": OPERATION,
        "wallet_balance_before": 56,
        "wallet_balance_after": 8,
        "reopened_yaba_kobo": 520000,
        "community_settled_kobo": 1000000,
        "refund_transaction_id": str(refund_id),
        "community_transaction_id": str(purchase_id),
        "community_payment_id": str(payment_id),
        "posts_cash": False,
    }
    if not apply:
        return result
    for (
        tid,
        kind,
        direction,
        amount,
        before,
        after,
        source,
        ref_type,
        ref_id,
        txn_meta,
        reversal,
    ) in [
        (
            refund_id,
            "refund",
            "credit",
            52,
            56,
            108,
            "sessions",
            "session_booking",
            OLD_BOOKING,
            {"operation": OPERATION},
            OLD_TXN,
        ),
        (
            purchase_id,
            "purchase",
            "debit",
            100,
            108,
            8,
            "events",
            "event",
            str(new["session_id"]),
            {
                "operation": OPERATION,
                "promo_bubbles": 0,
                "purchased_bubbles": 100,
                "award_transaction_id": AWARD_TXN,
            },
            None,
        ),
    ]:
        await db.execute(
            text(
                "INSERT INTO wallet_transactions (id,wallet_id,idempotency_key,transaction_type,direction,amount,balance_before,balance_after,status,description,service_source,reference_type,reference_id,initiated_by,txn_metadata,reversal_of_transaction_id,created_at,updated_at) VALUES (:id,:wallet,:key,:kind,:direction,:amount,:before,:after,'completed',:reason,:source,:ref_type,:ref_id,:actor,CAST(:meta AS jsonb),:reversal,now(),now())"
            ),
            {
                "id": tid,
                "wallet": uuid.UUID(WALLET),
                "key": f"{OPERATION}:{kind}",
                "kind": kind,
                "direction": direction,
                "amount": amount,
                "before": before,
                "after": after,
                "reason": REASON,
                "source": source,
                "ref_type": ref_type,
                "ref_id": ref_id,
                "actor": actor_label,
                "meta": json.dumps(txn_meta),
                "reversal": uuid.UUID(reversal) if reversal else None,
            },
        )
    await db.execute(
        text(
            "UPDATE wallet_transactions SET reversed_by_transaction_id=:refund,updated_at=now() WHERE id=:id"
        ),
        {"refund": refund_id, "id": uuid.UUID(OLD_TXN)},
    )
    await db.execute(
        text(
            "UPDATE wallets SET balance=8,lifetime_bubbles_received=lifetime_bubbles_received+52,lifetime_bubbles_spent=lifetime_bubbles_spent+100,updated_at=now() WHERE id=:id"
        ),
        {"id": uuid.UUID(WALLET)},
    )
    reversal = {
        "reason": "wallet_settlement_reversed",
        "operation": OPERATION,
        "audit_id": str(audit_id),
        "refund_transaction_id": str(refund_id),
        "refunded_bubbles": 52,
    }
    await db.execute(
        text(
            "UPDATE payments SET metadata=CAST(:meta AS jsonb),admin_review_note=:note,updated_at=now() WHERE id=:id"
        ),
        {
            "meta": json.dumps({**meta, "booking_reconciliation": reversal}),
            "note": f"wallet_settlement_reversed:{audit_id}",
            "id": uuid.UUID(OLD_PAYMENT),
        },
    )
    new_meta = {
        "booking_id": COMMUNITY_BOOKING,
        "session_id": str(new["session_id"]),
        "session_type": "event",
        "member_id": MEMBER,
        "bubbles_to_apply": 100,
        "bubbles_value_ngn": 10000,
        "wallet_transaction_id": str(purchase_id),
        "server_price": {"total_kobo": 1000000},
        "operation": OPERATION,
        "fulfillment": {"status": "applied", "attempts": 1, "next_retry_at": None},
    }
    await db.execute(
        text(
            "INSERT INTO payments (id,reference,member_auth_id,payer_email,purpose,amount,currency,status,payment_method,session_booking_id,paid_at,entitlement_applied_at,metadata,created_at,updated_at) VALUES (:id,:ref,:auth,:email,'session_booking',0,'NGN','paid','bubbles',:booking,now(),now(),CAST(:meta AS jsonb),now(),now())"
        ),
        {
            "id": payment_id,
            "ref": f"PAY-{payment_id.hex}",
            "auth": AUTH,
            "email": payment["payer_email"],
            "booking": uuid.UUID(COMMUNITY_BOOKING),
            "meta": json.dumps(new_meta),
        },
    )
    for bid, pid, tid, note in [
        (
            OLD_BOOKING,
            None,
            None,
            "Yaba fee reopened; original 52-Bubble payment reversed",
        ),
        (
            COMMUNITY_BOOKING,
            payment_id,
            purchase_id,
            "Community swim paid with the original 100-Bubble award",
        ),
    ]:
        await db.execute(
            text(
                "UPDATE session_bookings SET payment_intent_id=:payment,wallet_transaction_id=:txn,notes=concat_ws(E'\\n',nullif(notes,''),CAST(:note AS text)),updated_at=now() WHERE id=:id"
            ),
            {
                "payment": pid,
                "txn": tid,
                "note": f"[{OPERATION}] {note}",
                "id": uuid.UUID(bid),
            },
        )
        await db.execute(
            text(
                "UPDATE session_participants SET payment_status=:status,updated_at=now() WHERE booking_id=:id AND participant_kind='member'"
            ),
            {"status": "paid" if pid else "unpaid", "id": uuid.UUID(bid)},
        )
    await db.execute(
        text(
            "INSERT INTO wallet_audit_logs (id,domain,entity_type,entity_id,action,actor_label,old_value,new_value,reason,created_at) VALUES (:id,'wallet','wallet',:wallet,'wallet.session_settlement_reallocated',:actor,CAST(:old AS jsonb),CAST(:new AS jsonb),:reason,now())"
        ),
        {
            "id": audit_id,
            "wallet": uuid.UUID(WALLET),
            "actor": actor_label,
            "old": json.dumps(
                {
                    "wallet_balance": 56,
                    "yaba_payment_id": OLD_PAYMENT,
                    "yaba_transaction_id": OLD_TXN,
                    "payment_metadata": meta,
                    "admin_review_note": payment["admin_review_note"],
                }
            ),
            "new": json.dumps(result),
            "reason": REASON,
        },
    )
    # Explicit legacy mapping only for this reviewed spend. Normal runtime
    # emitters are unchanged. Never insert or update posted journal tables.
    today = (await rows(db, "SELECT current_date::text AS day"))[0]["day"]
    for tid, kind, amount, date, debit_account, credit_account, domain, ref in [
        (
            uuid.UUID(AWARD_TXN),
            "admin_adjustment",
            100,
            award["created_at"].date().isoformat(),
            "expense_marketing",
            "bubbles_liability",
            "community",
            COMMUNITY_BOOKING,
        ),
        (
            uuid.UUID(OLD_TXN),
            "purchase",
            52,
            debit["created_at"].date().isoformat(),
            "bubbles_liability",
            "revenue_club_session",
            "club",
            OLD_PAYMENT,
        ),
        (
            refund_id,
            "refund",
            52,
            None,
            "revenue_club_session",
            "bubbles_liability",
            "club",
            OLD_BOOKING,
        ),
        (
            purchase_id,
            "purchase",
            100,
            None,
            "bubbles_liability",
            "revenue_events",
            "events",
            COMMUNITY_BOOKING,
        ),
    ]:
        payload = {
            "entry_date": date or today,
            "description": f"Reviewed wallet {kind}: {REASON}",
            "source_service": "wallet",
            "source_type": kind,
            "source_id": str(tid),
            "calling_service": "wallet",
            "metadata": {"wallet_txn": str(tid), "operation": OPERATION},
            "lines": [
                {
                    "account_ref": debit_account,
                    "debit": amount * 10000,
                    "credit": 0,
                    "currency": "NGN",
                    "member_ref": MEMBER,
                    "dimension_1": domain,
                    "external_ref": ref,
                },
                {
                    "account_ref": credit_account,
                    "debit": 0,
                    "credit": amount * 10000,
                    "currency": "NGN",
                    "member_ref": MEMBER,
                    "dimension_1": domain,
                    "external_ref": ref,
                },
            ],
        }
        await db.execute(
            text(
                "INSERT INTO wallet_ledger_post_failures (id,idempotency_key,source_reference,payload,attempts,last_error,status,created_at,updated_at) VALUES (:id,:key,:ref,CAST(:payload AS jsonb),0,'Reviewed correction staged for ledger replay','pending',now(),now())"
            ),
            {
                "id": operation_id(f"ledger-{tid}"),
                "key": f"wallet:{kind}:{tid}",
                "ref": str(tid),
                "payload": json.dumps(payload),
            },
        )
    return result


async def replay_ledger(db: AsyncSession) -> list[dict]:
    """Replay only the four durable payloads belonging to this reviewed repair."""
    from libs.common.ledger_client import post_journal_entry

    audit = await rows(
        db, "SELECT id FROM wallet_audit_logs WHERE id=:id", id=operation_id("audit")
    )
    if not audit:
        raise ValueError("Apply the reviewed correction before replay")
    results = []
    for tid in [
        uuid.UUID(AWARD_TXN),
        uuid.UUID(OLD_TXN),
        operation_id("refund"),
        operation_id("community-purchase"),
    ]:
        found = await rows(
            db,
            "SELECT id,payload,status FROM wallet_ledger_post_failures WHERE id=:id",
            id=operation_id(f"ledger-{tid}"),
        )
        if (
            len(found) != 1
            or found[0]["payload"].get("metadata", {}).get("operation") != OPERATION
        ):
            raise ValueError("Expected durable journal payload missing or changed")
        row = found[0]
        if row["status"] == "replayed":
            results.append({"source_id": str(tid), "already_replayed": True})
            continue
        result = await post_journal_entry(**row["payload"])
        await db.execute(
            text(
                "UPDATE wallet_ledger_post_failures SET status='replayed',attempts=attempts+1,last_error=NULL,updated_at=now() WHERE id=:id"
            ),
            {"id": row["id"]},
        )
        await db.commit()
        results.append({"source_id": str(tid), **result})
    return results


async def run(args: argparse.Namespace) -> None:
    from libs.db.config import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        if args.replay_ledger:
            if args.apply:
                raise ValueError("Apply and journal replay are separate steps")
            print(json.dumps(await replay_ledger(db), default=str))
            return
        if not args.apply:
            await db.execute(text("SET TRANSACTION READ ONLY"))
        result = await repair(db, apply=args.apply, actor_label=args.actor_label)
        if args.apply:
            await db.commit()
        else:
            await db.rollback()
        print(json.dumps(result, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--replay-ledger", action="store_true")
    parser.add_argument("--actor-label")
    asyncio.run(run(parser.parse_args()))
