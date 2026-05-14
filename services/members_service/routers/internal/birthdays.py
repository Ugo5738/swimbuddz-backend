"""Birthday-reminder + admin-roster endpoints.

`/birthdays-today` and `/admins` are static-path; both must be registered
before any `/{member_id}` dynamic route in the aggregator.
"""

from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import Member, MemberProfile
from sqlalchemy import extract, select
from sqlalchemy.ext.asyncio import AsyncSession

from ._helpers import _ADMIN_REMINDER_ROLES, _LAGOS_TZ, _age_on
from ._schemas import AdminMember, BirthdayMember

router = APIRouter()


@router.get("/birthdays-today", response_model=List[BirthdayMember])
async def get_birthdays_today(
    on: Optional[date] = Query(
        None,
        description="Override target date (ISO YYYY-MM-DD). Defaults to today in Africa/Lagos.",
    ),
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return active members whose date_of_birth falls on the given date.

    Used by communications_service's daily birthday cron. The target date is
    resolved in Africa/Lagos so the cron can fire from any UTC offset and
    still match the human definition of "today" in Lagos.
    """
    target = on or datetime.now(_LAGOS_TZ).date()

    result = await db.execute(
        select(Member, MemberProfile)
        .join(MemberProfile, MemberProfile.member_id == Member.id)
        .where(
            Member.is_active.is_(True),
            Member.approval_status == "approved",
            MemberProfile.date_of_birth.is_not(None),
            extract("month", MemberProfile.date_of_birth) == target.month,
            extract("day", MemberProfile.date_of_birth) == target.day,
        )
    )

    rows = result.all()
    return [
        BirthdayMember(
            id=str(member.id),
            first_name=member.first_name,
            last_name=member.last_name,
            email=member.email,
            age=_age_on(profile.date_of_birth, target),
        )
        for member, profile in rows
    ]


@router.get("/admins", response_model=List[AdminMember])
async def get_admin_members(
    _: AuthUser = Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    """Return active members whose roles overlap with admin-flavoured roles.

    Used by communications_service to fan out admin-task notifications
    (e.g. the daily birthday WhatsApp reminder). Currently includes
    'admin', 'comms_admin', and 'community_manager'.
    """
    result = await db.execute(
        select(Member).where(
            Member.is_active.is_(True),
            Member.roles.overlap(list(_ADMIN_REMINDER_ROLES)),
        )
    )
    members = result.scalars().all()
    return [
        AdminMember(
            id=str(m.id),
            first_name=m.first_name,
            last_name=m.last_name,
            email=m.email,
            roles=list(m.roles or []),
        )
        for m in members
    ]
