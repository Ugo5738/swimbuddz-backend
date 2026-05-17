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
                    s.pay_band_min, s.pay_band_max
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
