"""Internal helper for emitting attendance milestone/streak reward events.

Called best-effort after each new PRESENT/LATE sign-in so it never blocks
the user's flow.
"""

import uuid

from libs.common.service_client import emit_rewards_event
from libs.common.datetime_utils import utc_now
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.attendance_service.models import AttendanceRecord, AttendanceStatus


async def _check_attendance_milestones(
    db: AsyncSession,
    member_id: uuid.UUID,
    member_auth_id: str,
) -> None:
    """Best-effort: check and emit attendance milestone/streak reward events.

    Called after each new PRESENT/LATE check-in.  Counts sessions in the
    current calendar month and emits ``attendance.monthly_milestone`` when
    thresholds (4, 8+) are first crossed.  Also checks consecutive-week
    streaks and emits ``attendance.streak`` at 4-week marks.
    """
    try:
        now = utc_now()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        # Count sessions attended this month
        month_count_result = await db.execute(
            select(func.count(AttendanceRecord.id)).where(
                AttendanceRecord.member_id == member_id,
                AttendanceRecord.status.in_(
                    [
                        AttendanceStatus.PRESENT,
                        AttendanceStatus.LATE,
                    ]
                ),
                AttendanceRecord.created_at >= month_start,
            )
        )
        session_count = month_count_result.scalar_one() or 0

        # Emit monthly milestone at threshold crossings (4 and 8)
        if session_count in (4, 8):
            month_key = now.strftime("%Y-%m")
            await emit_rewards_event(
                event_type="attendance.monthly_milestone",
                member_auth_id=member_auth_id,
                service_source="attendance",
                event_data={
                    "session_count": session_count,
                    "sessions": session_count,
                    "month": month_key,
                },
                idempotency_key=f"attendance-monthly-{member_auth_id}-{month_key}-{session_count}",
                calling_service="attendance",
            )

        # Check consecutive-week streak (simplified: count distinct weeks
        # with at least one PRESENT in the last 5 weeks)
        from datetime import timedelta

        five_weeks_ago = now - timedelta(weeks=5)
        week_result = await db.execute(
            select(
                func.count(
                    func.distinct(func.date_trunc("week", AttendanceRecord.created_at))
                )
            ).where(
                AttendanceRecord.member_id == member_id,
                AttendanceRecord.status.in_(
                    [
                        AttendanceStatus.PRESENT,
                        AttendanceStatus.LATE,
                    ]
                ),
                AttendanceRecord.created_at >= five_weeks_ago,
            )
        )
        consecutive_weeks = week_result.scalar_one() or 0

        # Emit streak reward at 4-week mark
        if consecutive_weeks >= 4:
            await emit_rewards_event(
                event_type="attendance.streak",
                member_auth_id=member_auth_id,
                service_source="attendance",
                event_data={
                    "consecutive_weeks": consecutive_weeks,
                    "streak_weeks": consecutive_weeks,
                },
                idempotency_key=f"attendance-streak-{member_auth_id}-{consecutive_weeks}w",
                calling_service="attendance",
            )
    except Exception:
        # Best-effort — never block the sign-in flow
        pass
