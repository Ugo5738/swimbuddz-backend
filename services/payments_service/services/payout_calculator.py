"""Coach payout calculator.

Computes per-block coach payouts using the formula:

    per_session_per_student = cohort_price × band_pct ÷ total_blocks ÷ sessions_in_block
    per_student_block_total = per_session_per_student × delivered_sessions
    block_total = Σ student_totals + makeup_credits

Defaults to "present unless marked otherwise" — paid+enrolled students count
as having attended each session in the block by default. The coach only marks
exceptions: EXCUSED (with notice → make-up owed) or ABSENT (no notice → still
counted as a held session for pay purposes).

Make-ups completed during the block also generate pay credits attributed to
the block in which they were delivered.

This module reads cross-service tables (cohorts, sessions, enrollments,
attendance_records) directly via SQL — they all live in the same `public`
schema. Writes are scoped to payments_service tables only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional

from libs.common.logging import get_logger
from services.payments_service.models import (
    CohortMakeupObligation,
    MakeupReason,
    MakeupStatus,
    RecurringPayoutConfig,
)
from sqlalchemy import bindparam, select, text
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)


# Statuses that count as "session was held for this student" — coach earns pay.
DELIVERED_STATUSES = ("present", "late", "absent")
# Status that marks an excused absence (with notice). No pay; make-up owed.
EXCUSED_STATUS = "excused"
# Status that means the session itself was cancelled (not held). No pay.
CANCELLED_STATUS = "cancelled"


@dataclass(frozen=True)
class StudentBlockLine:
    """Computed contribution of a single student to a block payout."""

    student_member_id: uuid.UUID
    student_name: Optional[str]
    enrolled_at: datetime
    sessions_in_block: int  # Total sessions the cohort ran in this block window
    sessions_eligible: int  # Sessions that occurred AFTER the student enrolled
    sessions_delivered: int  # Eligible sessions − excused absences
    sessions_excused: int  # Sessions where coach marked EXCUSED
    makeups_completed: int  # Make-ups delivered for this student in this block
    per_session_amount_kobo: int
    student_total_kobo: int


@dataclass(frozen=True)
class BlockPayoutComputation:
    """Full result of computing a block payout."""

    config_id: uuid.UUID
    coach_member_id: uuid.UUID
    cohort_id: uuid.UUID
    block_index: int
    block_start: datetime
    block_end: datetime
    per_session_amount_kobo: int
    lines: List[StudentBlockLine]
    total_kobo: int
    sessions_in_block: int
    new_makeup_obligations: List[dict]  # To be created in CohortMakeupObligation


def _block_window(config: RecurringPayoutConfig, block_index: int) -> tuple[datetime, datetime]:
    """Return [start, end) for a given block index of a cohort."""
    delta = timedelta(days=config.block_length_days)
    start = config.cohort_start_date + delta * block_index
    end = start + delta
    return start, end


async def _count_sessions_in_block(
    db: AsyncSession,
    cohort_id: uuid.UUID,
    block_start: datetime,
    block_end: datetime,
) -> List[dict]:
    """Return sessions in the cohort that started in [block_start, block_end).

    Excludes sessions whose status is `cancelled` (they didn't happen).
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT id, starts_at, status
                FROM public.sessions
                WHERE cohort_id = :cohort_id
                  AND starts_at >= :start
                  AND starts_at < :end
                  AND COALESCE(status, '') <> :cancelled
                ORDER BY starts_at
                """
            ),
            {
                "cohort_id": cohort_id,
                "start": block_start,
                "end": block_end,
                "cancelled": CANCELLED_STATUS,
            },
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def _active_enrollments(
    db: AsyncSession,
    cohort_id: uuid.UUID,
    as_of: datetime,
) -> List[dict]:
    """Enrollments considered active as of `as_of`.

    Includes statuses: enrolled, dropout_pending, dropped, graduated.
    A student who dropped mid-cohort is still included so the coach can
    be credited for sessions they attended before dropping; the per-student
    computation clips eligible sessions at `dropped_at`. Excludes:
      - pending_approval, waitlist (never actually attended)
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT e.id AS enrollment_id, e.member_id, e.status,
                       e.created_at AS row_created_at,
                       e.enrolled_at, e.dropped_at,
                       m.first_name, m.last_name
                FROM public.enrollments e
                LEFT JOIN public.members m ON m.id = e.member_id
                WHERE e.cohort_id = :cohort_id
                  AND e.created_at < :as_of
                  AND e.status IN (
                      'enrolled', 'dropout_pending', 'dropped', 'graduated'
                  )
                ORDER BY e.created_at
                """
            ),
            {"cohort_id": cohort_id, "as_of": as_of},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def _attendance_for_student_in_sessions(
    db: AsyncSession,
    member_id: uuid.UUID,
    session_ids: List[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Return {session_id: status} for explicitly-recorded attendance rows.

    Sessions without a row default to PRESENT (handled by caller).
    """
    if not session_ids:
        return {}
    stmt = text(
        """
        SELECT session_id, status
        FROM public.attendance_records
        WHERE member_id = :member_id
          AND session_id IN :session_ids
        """
    ).bindparams(bindparam("session_ids", expanding=True))
    rows = (
        await db.execute(
            stmt,
            {"member_id": member_id, "session_ids": session_ids},
        )
    ).mappings().all()
    return {r["session_id"]: (r["status"] or "").lower() for r in rows}


async def _completed_makeups_in_block(
    db: AsyncSession,
    config: RecurringPayoutConfig,
    block_start: datetime,
    block_end: datetime,
) -> dict[uuid.UUID, int]:
    """Return {student_member_id: count} of makeups completed in this block.

    A make-up is "completed in this block" when its `completed_at` falls in
    [block_start, block_end). Each completed make-up = one extra paid session.
    """
    rows = (
        await db.execute(
            select(
                CohortMakeupObligation.student_member_id,
            ).where(
                CohortMakeupObligation.cohort_id == config.cohort_id,
                CohortMakeupObligation.coach_member_id == config.coach_member_id,
                CohortMakeupObligation.status == MakeupStatus.COMPLETED,
                CohortMakeupObligation.completed_at >= block_start,
                CohortMakeupObligation.completed_at < block_end,
                CohortMakeupObligation.pay_credited_in_payout_id.is_(None),
            )
        )
    ).all()
    counts: dict[uuid.UUID, int] = {}
    for (student_id,) in rows:
        counts[student_id] = counts.get(student_id, 0) + 1
    return counts


def _per_session_amount_kobo(
    config: RecurringPayoutConfig, sessions_in_block: int
) -> int:
    """Compute pay rate per (student, session) for a given block.

    Formula: cohort_price × band_pct ÷ total_blocks ÷ sessions_in_block

    The per-session rate is calculated from the *expected* sessions in this
    specific block. If a block has zero sessions (e.g. all cancelled), no
    per-session pay accrues — only make-up credits computed elsewhere.
    """
    if sessions_in_block <= 0:
        return 0
    band = Decimal(config.band_percentage) / Decimal(100)
    block_share = Decimal(config.cohort_price_amount) * band / Decimal(config.total_blocks)
    per_session = block_share / Decimal(sessions_in_block)
    return int(per_session.quantize(Decimal("1")))  # floor to whole kobo


async def compute_block_payout(
    db: AsyncSession,
    config: RecurringPayoutConfig,
    block_index: int,
) -> BlockPayoutComputation:
    """Compute a single block payout for a recurring config.

    Does not write any rows. Caller is responsible for inserting the
    CoachPayout and any new make-up obligations atomically.
    """
    block_start, block_end = _block_window(config, block_index)

    sessions = await _count_sessions_in_block(db, config.cohort_id, block_start, block_end)
    session_ids = [s["id"] for s in sessions]
    sessions_in_block = len(sessions)

    enrollments = await _active_enrollments(db, config.cohort_id, as_of=block_end)
    makeup_counts = await _completed_makeups_in_block(db, config, block_start, block_end)

    per_session_kobo = _per_session_amount_kobo(config, sessions_in_block)

    lines: List[StudentBlockLine] = []
    new_makeup_obligations: List[dict] = []

    for enr in enrollments:
        student_id: uuid.UUID = enr["member_id"]
        # Prefer the explicit `enrolled_at` (set when the student officially
        # joins the cohort); fall back to row creation time.
        enrolled_at: datetime = enr.get("enrolled_at") or enr["row_created_at"]
        if enrolled_at.tzinfo is None:
            enrolled_at = enrolled_at.replace(tzinfo=timezone.utc)

        # If the student dropped, clip the eligible window at the drop date so
        # the coach is not paid for sessions a dropout could not have attended.
        dropped_at: Optional[datetime] = enr.get("dropped_at")
        if dropped_at is not None and dropped_at.tzinfo is None:
            dropped_at = dropped_at.replace(tzinfo=timezone.utc)

        def _is_eligible(session: dict) -> bool:
            if session["starts_at"] <= enrolled_at:
                return False
            if dropped_at is not None and session["starts_at"] >= dropped_at:
                return False
            return True

        # Eligible sessions: those that started AFTER the student enrolled
        # AND BEFORE they dropped (if applicable).
        eligible_sessions = [s for s in sessions if _is_eligible(s)]
        # Ineligible-due-to-late-join: sessions before enrollment. These
        # become LATE_JOIN make-up obligations.
        ineligible_sessions = [s for s in sessions if s["starts_at"] <= enrolled_at]

        # Late-join: each session that ran before the student enrolled is a
        # make-up obligation owed.
        for s in ineligible_sessions:
            new_makeup_obligations.append({
                "cohort_id": config.cohort_id,
                "student_member_id": student_id,
                "coach_member_id": config.coach_member_id,
                "original_session_id": s["id"],
                "reason": MakeupReason.LATE_JOIN,
            })

        # Look up explicit attendance for the eligible sessions.
        attendance = await _attendance_for_student_in_sessions(
            db, student_id, [s["id"] for s in eligible_sessions]
        )

        excused_count = 0
        delivered_count = 0
        for s in eligible_sessions:
            status = attendance.get(s["id"])  # None if no row recorded
            if status == EXCUSED_STATUS:
                excused_count += 1
                # Excused = make-up owed
                new_makeup_obligations.append({
                    "cohort_id": config.cohort_id,
                    "student_member_id": student_id,
                    "coach_member_id": config.coach_member_id,
                    "original_session_id": s["id"],
                    "reason": MakeupReason.EXCUSED_ABSENCE,
                })
            elif status == CANCELLED_STATUS:
                # Session cancelled at attendance level; not delivered, not excused.
                pass
            else:
                # PRESENT / LATE / ABSENT (no notice) / no record → delivered.
                delivered_count += 1

        makeups_completed = makeup_counts.get(student_id, 0)
        student_total = (delivered_count + makeups_completed) * per_session_kobo

        student_name = None
        if enr.get("first_name") or enr.get("last_name"):
            student_name = f"{enr.get('first_name') or ''} {enr.get('last_name') or ''}".strip() or None

        lines.append(
            StudentBlockLine(
                student_member_id=student_id,
                student_name=student_name,
                enrolled_at=enrolled_at,
                sessions_in_block=sessions_in_block,
                sessions_eligible=len(eligible_sessions),
                sessions_delivered=delivered_count,
                sessions_excused=excused_count,
                makeups_completed=makeups_completed,
                per_session_amount_kobo=per_session_kobo,
                student_total_kobo=student_total,
            )
        )

    total_kobo = sum(line.student_total_kobo for line in lines)

    logger.info(
        "Computed block payout: config=%s block=%s sessions=%d students=%d "
        "per_session=%d kobo total=%d kobo new_makeups=%d",
        config.id,
        block_index,
        sessions_in_block,
        len(lines),
        per_session_kobo,
        total_kobo,
        len(new_makeup_obligations),
    )

    return BlockPayoutComputation(
        config_id=config.id,
        coach_member_id=config.coach_member_id,
        cohort_id=config.cohort_id,
        block_index=block_index,
        block_start=block_start,
        block_end=block_end,
        per_session_amount_kobo=per_session_kobo,
        lines=lines,
        total_kobo=total_kobo,
        sessions_in_block=sessions_in_block,
        new_makeup_obligations=new_makeup_obligations,
    )


def block_window(config: RecurringPayoutConfig, block_index: int) -> tuple[datetime, datetime]:
    """Public alias for _block_window (used by callers that need the window
    without computing a full payout)."""
    return _block_window(config, block_index)
