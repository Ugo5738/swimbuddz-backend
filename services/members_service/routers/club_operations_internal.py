"""Members owns pod authority and the commercial boundaries of a promised swim."""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from libs.auth.dependencies import require_service_role
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.members_service.models import (
    Club,
    ClubPlanSession,
    ClubPlanVersion,
    Member,
    Pod,
)
from services.members_service.services.club_access import resolve_club_access_checks
from services.members_service.routers.internal._schemas import ClubAccessCheck

router = APIRouter(
    prefix="/internal/clubs/operations",
    dependencies=[Depends(require_service_role)],
    tags=["internal-club-operations"],
)


class PodSchedulingAuthorization(BaseModel):
    auth_id: str
    pod_id: uuid.UUID


@router.post("/authorize-pod")
async def authorize_pod(
    body: PodSchedulingAuthorization, db: AsyncSession = Depends(get_async_db)
):
    pod = await db.get(Pod, body.pod_id)
    member = (
        await db.execute(select(Member).where(Member.auth_id == body.auth_id))
    ).scalar_one_or_none()
    if (
        not pod
        or not member
        or not member.is_active
        or member.id not in {pod.pod_lead_id, pod.assistant_pod_lead_id}
        or pod.status.value != "active"
    ):
        raise HTTPException(
            403, "Only this active pod's lead or assistant may schedule its practices"
        )
    club = await db.get(Club, pod.club_id)
    access = await resolve_club_access_checks(
        db,
        [
            ClubAccessCheck(
                context_key="pod-scheduling",
                member_id=member.id,
                club_id=pod.club_id,
                club_access_mode="active_club",
                at=utc_now(),
                pod_id=pod.id,
                pool_id=pod.default_pool_id or club.default_pool_id if club else None,
            )
        ],
    )
    if not club or not club.is_active or not access[0]["allowed"]:
        raise HTTPException(
            403, "Active Club eligibility is required to lead practices"
        )
    return {
        "pod_id": str(pod.id),
        "club_id": str(club.id),
        "pool_id": str(pod.default_pool_id or club.default_pool_id),
        "capacity": pod.max_size,
        "member_id": str(member.id),
    }


class PromiseCheck(BaseModel):
    session_id: uuid.UUID
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    pool_id: uuid.UUID | None = None


@router.post("/promises")
async def check_promises(body: PromiseCheck, db: AsyncSession = Depends(get_async_db)):
    rows = (
        await db.execute(
            select(ClubPlanVersion, ClubPlanSession)
            .join(
                ClubPlanSession,
                ClubPlanSession.plan_version_id == ClubPlanVersion.id,
            )
            .where(
                ClubPlanSession.session_id == body.session_id,
                ClubPlanVersion.published_at.is_not(None),
            )
        )
    ).all()
    if body.starts_at:
        start = body.starts_at.astimezone(ZoneInfo("Africa/Lagos")).date()
        end = (
            (body.ends_at or body.starts_at).astimezone(ZoneInfo("Africa/Lagos")).date()
        )
        for plan, link in rows:
            if not plan.period_start <= start <= end <= plan.period_end or (
                body.pool_id and body.pool_id != link.pool_id
            ):
                raise HTTPException(
                    409,
                    "Keep the promised swim in its purchased quarter and pool; another period/venue requires an Admin coverage agreement",
                )
    return {
        "published_promise": bool(rows),
        "plan_ids": [str(plan.id) for plan, _ in rows],
    }
