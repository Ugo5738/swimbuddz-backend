"""Review the two owner-identified VI 2 individual extras from 10/17 October.

Dry-run by default. Run only after the attendance-mode migration/deployment.
The owner confirmed Bello attended 10 October and Isabella has explicit class
allocations. Preserve all bookings, allocations and manual attendance decisions.
Only an unassigned, worker-created Present row for Ajibola can be removed.
Save stdout as the before/after operational audit when applying.
"""

import argparse
import asyncio
import json
import uuid

from sqlalchemy import bindparam, text

from libs.common.datetime_utils import utc_now
from libs.db.config import AsyncSessionLocal

COHORT_ID = "758520af-3a10-48a3-a34a-493c11a95dcd"
SESSION_IDS = [
    "e05dfa4d-5223-4307-aa2e-a10a7c3e776b",
    "5767a832-5e4b-4a39-895c-31e206d72dbf",
]
BELLO = "5ae4d098-a68e-4bba-a116-b31914ae100f"
AJIBOLA = "b97c80fa-6f3a-423e-8f26-0d68ce5f5a45"
DEFAULT_NOTE = "Academy default Present; coach/admin exceptions override"


async def review(*, apply: bool, actor_auth_id: str | None) -> None:
    if apply and not actor_auth_id:
        raise ValueError("Applying requires --actor-auth-id for the audit")
    operation_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as db:
        if not apply:
            await db.execute(text("SET TRANSACTION READ ONLY"))
        params = {"ids": SESSION_IDS, "cohort": COHORT_ID}
        statement = text(
            "SELECT id,cohort_id,starts_at,cohort_fee_mode,cohort_attendance_mode "
            "FROM sessions WHERE id IN :ids ORDER BY starts_at"
            + (" FOR UPDATE" if apply else "")
        ).bindparams(bindparam("ids", expanding=True))
        sessions = list((await db.execute(statement, params)).mappings())
        if len(sessions) != 2 or any(
            str(row["cohort_id"]) != COHORT_ID
            or row["cohort_fee_mode"] != "included"
            or row["starts_at"].date().isoformat() != expected
            for row, expected in zip(sessions, ["2026-10-10", "2026-10-17"])
        ):
            raise ValueError("The two reviewed extra sessions no longer match")
        candidates = text(
            "SELECT a.id,a.session_id,a.member_id,a.status::text FROM attendance_records a "
            "WHERE a.session_id IN :ids AND a.member_id = :member "
            "AND a.status = 'present' AND a.notes = :note AND a.booking_id IS NULL "
            "AND NOT EXISTS (SELECT 1 FROM session_bookings b "
            "WHERE b.session_id=a.session_id AND b.member_id=a.member_id) "
            "AND NOT EXISTS (SELECT 1 FROM academy_class_allocations l "
            "WHERE l.session_id=a.session_id AND l.member_id=a.member_id "
            "AND l.status <> 'reversed')" + (" FOR UPDATE OF a" if apply else "")
        ).bindparams(bindparam("ids", expanding=True))
        rows = list(
            (
                await db.execute(
                    candidates,
                    {"ids": SESSION_IDS, "member": AJIBOLA, "note": DEFAULT_NOTE},
                )
            ).mappings()
        )
        report = {
            "operation_id": operation_id,
            "at": utc_now().isoformat(),
            "actor_auth_id": actor_auth_id,
            "apply": apply,
            "reason": "Owner confirmed extra classes require individual attendance; remove unassigned automatic presence, preserving verified participants.",
            "sessions_before": [dict(row) for row in sessions],
            "automatic_rows_to_remove": [dict(row) for row in rows],
        }
        print(json.dumps({"before": report}, default=str), flush=True)
        if not apply:
            await db.rollback()
            return
        await db.execute(
            text(
                "UPDATE sessions SET cohort_attendance_mode='individual',updated_at=now() "
                "WHERE id IN :ids AND cohort_id=:cohort"
            ).bindparams(bindparam("ids", expanding=True)),
            params,
        )
        # Bello's 10 October attendance is owner-confirmed, not an inferred fact.
        changed = await db.execute(
            text(
                "UPDATE attendance_records SET notes=:verified,updated_at=now() "
                "WHERE session_id=:sid AND member_id=:mid AND status='present' "
                "AND notes=:default"
            ),
            {
                "sid": SESSION_IDS[0],
                "mid": BELLO,
                "default": DEFAULT_NOTE,
                "verified": "Owner-confirmed attendance on 10 October; included in reviewed historical class usage. Six attended classes through 25 October. Review "
                + operation_id,
            },
        )
        removed = 0
        for row in rows:
            result = await db.execute(
                text("DELETE FROM attendance_records WHERE id=:id"), {"id": row["id"]}
            )
            removed += result.rowcount
        await db.commit()
        print(
            json.dumps(
                {
                    "after": {
                        "operation_id": operation_id,
                        "sessions_set_individual": SESSION_IDS,
                        "verified_bello_rows": changed.rowcount,
                        "removed_unassigned_automatic_rows": removed,
                        "bookings_and_allocations_changed": 0,
                    }
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor-auth-id")
    args = parser.parse_args()
    asyncio.run(review(apply=args.apply, actor_auth_id=args.actor_auth_id))
