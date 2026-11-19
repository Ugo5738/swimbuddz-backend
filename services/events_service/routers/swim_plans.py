"""Audited, request-only member swim planning with admin-controlled publication.

This route MUST NOT create payment intents, reserve pool capacity, or suggest
an unverified timeslot is available. Admin verification records an external
partner reservation reference; venue approval is distinct from event approval.
"""
from datetime import timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_partner_pool
from libs.db.session import get_async_db
from services.events_service.models import Event, MemberRef
from services.events_service.models.swim_plan import (
    SwimPlan,
    SwimPlanAudit,
    SwimPublicationPolicy,
)
from services.events_service.routers.member import (
    _require_adult,
    _snapshot_pool_fee,
    get_current_member,
)

router = APIRouter(prefix="/events/swim-plans", tags=["swim-plans"])


class PlanCreate(BaseModel):
    kind: Literal["solo", "friends", "open", "coached"]
    title: str = Field(min_length=3, max_length=160)
    notes: str | None = Field(default=None, max_length=1000)
    pool_id: UUID | None = None
    location: str | None = Field(default=None, max_length=255)
    starts_at: str
    ends_at: str
    participants: int = Field(default=1, ge=1, le=25)
    coaching_focus: str | None = Field(default=None, max_length=250)

    @model_validator(mode="after")
    def validate_plan(self):
        from datetime import datetime

        try:
            start = datetime.fromisoformat(self.starts_at.replace("Z", "+00:00"))
            end = datetime.fromisoformat(self.ends_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("Use ISO-8601 date and time values") from exc
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("Start and end times require time zones")
        if start <= utc_now() + timedelta(minutes=15):
            raise ValueError("Choose a future start at least 15 minutes away")
        if not timedelta(minutes=30) <= (end - start) <= timedelta(hours=6):
            raise ValueError("A swim must last between 30 minutes and 6 hours")
        if self.kind == "open" and self.pool_id is None:
            raise ValueError("Public Open Swims require a partner pool to verify")
        if self.kind == "solo" and self.participants != 1:
            raise ValueError("A solo swim has one swimmer; use friends for a group")
        if self.kind != "coached" and self.coaching_focus:
            raise ValueError("Coaching focus is only valid for coached sessions")
        if self.pool_id is None and not self.location:
            raise ValueError("Choose a partner pool or describe a proposed venue")
        return self


class PolicyUpdate(BaseModel):
    auto_approve_public: bool


class PlanReview(BaseModel):
    approved: bool
    notes: str | None = Field(default=None, max_length=1000)


class VenueVerification(BaseModel):
    confirmed: bool
    reference: str | None = Field(default=None, max_length=255)
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_partner_reference(self):
        if self.confirmed and len((self.reference or "").strip()) < 8:
            raise ValueError("Record the partner's booking confirmation reference")
        return self


def _initial_approval(kind: str, auto_approve: bool) -> str:
    return "approved" if kind != "open" or auto_approve else "pending"


def _plan_data(plan: SwimPlan) -> dict:
    return {
        "id": str(plan.id),
        "member_id": str(plan.member_id),
        "kind": plan.kind,
        "title": plan.title,
        "notes": plan.notes,
        "pool_id": str(plan.pool_id) if plan.pool_id else None,
        "location": plan.location,
        "starts_at": plan.starts_at.isoformat(),
        "ends_at": plan.ends_at.isoformat(),
        "participants": plan.participants,
        "coaching_focus": plan.coaching_focus,
        "approval_status": plan.approval_status,
        "venue_status": plan.venue_status,
        "status": plan.status,
        "linked_event_id": str(plan.linked_event_id) if plan.linked_event_id else None,
        "admin_notes": plan.admin_notes,
        "venue_confirmation_reference": plan.venue_confirmation_reference,
    }


async def _policy(db: AsyncSession) -> SwimPublicationPolicy:
    policy = await db.get(SwimPublicationPolicy, 1)
    if policy is None:
        # Migration seeds this row. Treat absence as the documented default.
        policy = SwimPublicationPolicy(id=1, auto_approve_public=True)
        db.add(policy)
        await db.flush()
    return policy


async def _audit(
    db: AsyncSession,
    plan: SwimPlan | None,
    actor: str,
    action: str,
    old: str | None = None,
    new: str | None = None,
):
    db.add(
        SwimPlanAudit(
            plan_id=plan.id if plan else None,
            actor_auth_id=actor,
            action=action,
            old_value=old,
            new_value=new,
        )
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_plan(
    request: PlanCreate,
    member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    await _require_adult(member.id)
    if request.pool_id:
        pool = await get_partner_pool(str(request.pool_id), calling_service="events")
        if not pool:
            raise HTTPException(
                422, "Only active partner pools can be selected. Try another pool or location."
            )
    policy = await _policy(db)
    approval = _initial_approval(request.kind, policy.auto_approve_public)
    from datetime import datetime

    plan = SwimPlan(
        member_id=member.id,
        kind=request.kind,
        title=request.title.strip(),
        notes=request.notes,
        pool_id=request.pool_id,
        location=request.location,
        starts_at=datetime.fromisoformat(request.starts_at.replace("Z", "+00:00")),
        ends_at=datetime.fromisoformat(request.ends_at.replace("Z", "+00:00")),
        participants=request.participants,
        coaching_focus=request.coaching_focus,
        approval_status=approval,
        venue_status="pending",
        status="pending_availability",
    )
    db.add(plan)
    await db.flush()
    await _audit(db, plan, member.auth_id, "create", new=approval)
    await db.commit()
    await db.refresh(plan)
    # This is NEVER a guaranteed booking. Approval only concerns publication.
    return _plan_data(plan)


@router.get("/me")
async def my_plans(
    member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        await db.execute(
            select(SwimPlan)
            .where(SwimPlan.member_id == member.id)
            .order_by(SwimPlan.created_at.desc())
            .limit(100)
        )
    ).scalars().all()
    return [_plan_data(row) for row in rows]


@router.get("/admin/policy")
async def get_policy(
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    policy = await _policy(db)
    return {"auto_approve_public": policy.auto_approve_public}


@router.put("/admin/policy")
async def set_policy(
    payload: PolicyUpdate,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    policy = await _policy(db)
    before = str(policy.auto_approve_public)
    policy.auto_approve_public = payload.auto_approve_public
    policy.updated_by = admin.user_id
    await _audit(
        db, None, admin.user_id, "policy_change", old=before, new=str(payload.auto_approve_public)
    )
    await db.commit()
    return {"auto_approve_public": policy.auto_approve_public}


@router.get("/admin")
async def admin_plans(
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    rows = (
        await db.execute(
            select(SwimPlan).order_by(SwimPlan.created_at.desc()).limit(200)
        )
    ).scalars().all()
    return [_plan_data(row) for row in rows]


async def _load_locked(db: AsyncSession, plan_id: UUID) -> SwimPlan:
    row = (
        await db.execute(
            select(SwimPlan).where(SwimPlan.id == plan_id).with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Swim plan not found")
    return row


@router.put("/admin/{plan_id}/review")
async def review_plan(
    plan_id: UUID,
    payload: PlanReview,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    plan = await _load_locked(db, plan_id)
    if plan.linked_event_id:
        raise HTTPException(409, "Published plans cannot be re-approved here")
    before = plan.approval_status
    plan.approval_status = "approved" if payload.approved else "rejected"
    plan.admin_notes = payload.notes
    if not payload.approved:
        plan.status = "rejected"
    await _audit(db, plan, admin.user_id, "review", old=before, new=plan.approval_status)
    await db.commit()
    return _plan_data(plan)


@router.put("/admin/{plan_id}/verify-venue")
async def verify_venue(
    plan_id: UUID,
    payload: VenueVerification,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    plan = await _load_locked(db, plan_id)
    if plan.linked_event_id:
        raise HTTPException(409, "Published plans cannot be changed here")
    previous = plan.venue_status
    plan.venue_status = "verified" if payload.confirmed else "rejected"
    plan.venue_confirmation_reference = payload.reference if payload.confirmed else None
    plan.verified_by = admin.user_id
    plan.admin_notes = payload.notes
    if not payload.confirmed:
        plan.status = "pending_availability"
    await _audit(db, plan, admin.user_id, "venue_verification", old=previous, new=plan.venue_status)
    await db.commit()
    return _plan_data(plan)


@router.post("/admin/{plan_id}/publish")
async def publish_open_plan(
    plan_id: UUID,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    plan = await _load_locked(db, plan_id)
    if plan.kind != "open":
        raise HTTPException(409, "Only a public Open Swim creates an Event")
    if plan.linked_event_id:
        return _plan_data(plan)
    if plan.approval_status != "approved" or plan.venue_status != "verified":
        raise HTTPException(409, "Both publication approval and verified pool time are required")
    if not plan.pool_id or not plan.venue_confirmation_reference:
        raise HTTPException(409, "A partner pool and reservation reference are required")
    if plan.starts_at <= utc_now():
        raise HTTPException(409, "Cannot publish a past swim")
    pool_fee_kobo, pool = await _snapshot_pool_fee(plan.pool_id)
    event = Event(
        title=plan.title,
        description=plan.notes,
        event_type="open_swim",
        location=plan.location or str(pool.get("name") or "Partner pool"),
        start_time=plan.starts_at,
        end_time=plan.ends_at,
        max_capacity=plan.participants,
        pool_id=plan.pool_id,
        pool_fee_kobo=pool_fee_kobo,
        organizer_surcharge_kobo=0,
        created_by=plan.member_id,
        visibility="members_only",
        tier_access="community",
        status="published",
    )
    db.add(event)
    await db.flush()
    plan.linked_event_id = event.id
    plan.status = "published"
    await _audit(db, plan, admin.user_id, "publish", new=str(event.id))
    await db.commit()
    return _plan_data(plan)
