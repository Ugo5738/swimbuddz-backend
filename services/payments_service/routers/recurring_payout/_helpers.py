"""Private helpers shared across the recurring-payout submodules."""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from libs.auth.models import AuthUser
from libs.common.service_client import get_member_by_auth_id
from sqlalchemy import text


async def _fetch_cohort_snapshot(db, cohort_id: uuid.UUID) -> dict:
    """Fetch cohort fields needed to seed a recurring config snapshot.

    Reads the cohorts and programs tables directly (same DB). Returns:
      - start_date, end_date, total_blocks, block_length_days
      - cohort_price_amount (in kobo), currency
      - required_coach_grade (for sanity check / audit)
      - pay_band_min, pay_band_max (from complexity score)
    """
    row = (
        (
            await db.execute(
                text(
                    """
                SELECT
                    c.id, c.start_date, c.end_date, c.required_coach_grade,
                    COALESCE(c.price_override, p.price_amount) AS price_amount,
                    p.currency,
                    p.duration_weeks,
                    s.pay_band_min, s.pay_band_max,
                    -- Planned class count = week-numbered cohort_class sessions
                    -- (ad-hoc make-ups are created without a week_number). This
                    -- is the per-class-rate denominator, robust to cadence
                    -- (1x vs 2x/week). See COACH_PAYOUT_REDESIGN.md §2.2.
                    (
                        SELECT count(*) FROM public.sessions ss
                        WHERE ss.cohort_id = c.id
                          AND ss.session_type = 'cohort_class'
                          AND ss.week_number IS NOT NULL
                    ) AS planned_class_count
                FROM public.cohorts c
                JOIN public.programs p ON p.id = c.program_id
                LEFT JOIN public.cohort_complexity_scores s
                    ON s.cohort_id = c.id
                WHERE c.id = :cohort_id
                """
                ),
                {"cohort_id": cohort_id},
            )
        )
        .mappings()
        .first()
    )
    if not row:
        return {}
    return dict(row)


async def _active_paid_coach_count(db, cohort_id: uuid.UUID) -> int:
    """Count ACTIVE paid coach assignments (lead + assistant) on a cohort.

    Drives the main/assistant pay split: 1 → that coach gets the full band,
    2 → 70/30 lead/assistant. Shadow/observer roles are unpaid and excluded.
    Cancelled/completed assignments don't count. Reads the academy-owned
    coach_assignments table directly (same DB), matching _fetch_cohort_snapshot.
    """
    row = (
        await db.execute(
            text(
                """
                SELECT count(*) AS n FROM public.coach_assignments
                WHERE cohort_id = :cohort_id
                  AND status = 'active'
                  AND is_session_override = false
                  AND role IN ('lead', 'assistant')
                """
            ),
            {"cohort_id": cohort_id},
        )
    ).scalar_one()
    return int(row or 0)


async def _resolve_coach_member_id(current_user: AuthUser) -> uuid.UUID:
    """Resolve the calling user's member_id, requiring the coach role."""
    if not current_user.has_role("coach"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Coach role required",
        )
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="payments"
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Member profile not found",
        )
    return uuid.UUID(member["id"])
