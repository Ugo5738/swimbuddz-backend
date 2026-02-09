"""Admin members router - approval and administrative operations."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.emails.client import get_email_client
from libs.db.session import get_async_db
from pydantic import BaseModel
from services.members_service.models import Member, MemberMembership
from services.members_service.routers._helpers import member_eager_load_options
from services.members_service.schemas import (
    ActivateClubRequest,
    ActivateCommunityRequest,
    ApprovalAction,
    ExtendCommunityRequest,
    MemberResponse,
    PendingMemberResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin/members", tags=["admin-members"])


@router.get("/pending", response_model=List[PendingMemberResponse])
async def list_pending_members(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """List all members with pending approval status (admin only)."""
    query = (
        select(Member)
        .where(Member.approval_status == "pending")
        .options(*member_eager_load_options())
        .order_by(Member.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/by-email/{email}", response_model=MemberResponse)
async def get_member_by_email(
    email: str,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get a member by email (admin only)."""
    query = (
        select(Member)
        .where(func.lower(Member.email) == email.lower())
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    return member


@router.post("/{member_id}/approve", response_model=MemberResponse)
async def approve_member(
    member_id: uuid.UUID,
    action: ApprovalAction,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Approve a pending member registration (admin only)."""
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    if member.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Member is already {member.approval_status}",
        )

    member.approval_status = "approved"
    member.approved_at = datetime.now(timezone.utc)
    member.approved_by = current_user.email
    if action.notes:
        member.approval_notes = action.notes

    db.add(member)
    await db.commit()
    await db.refresh(member)

    # Send approval email notification via centralized email service
    email_client = get_email_client()
    await email_client.send_template(
        template_type="member_approved",
        to_email=member.email,
        template_data={
            "member_name": member.first_name,
        },
    )

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.post("/{member_id}/reject", response_model=MemberResponse)
async def reject_member(
    member_id: uuid.UUID,
    action: ApprovalAction,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Reject a pending member registration (admin only).
    User can reapply later.
    """
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    if member.approval_status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Member is already {member.approval_status}",
        )

    member.approval_status = "rejected"
    member.approved_at = datetime.now(timezone.utc)
    member.approved_by = current_user.email
    if action.notes:
        member.approval_notes = action.notes

    db.add(member)
    await db.commit()
    await db.refresh(member)

    # Send rejection email notification via centralized email service
    email_client = get_email_client()
    await email_client.send_template(
        template_type="member_rejected",
        to_email=member.email,
        template_data={
            "member_name": member.first_name,
            "rejection_reason": action.notes or "Does not meet current criteria",
        },
    )

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.post("/{member_id}/approve-upgrade", response_model=MemberResponse)
async def approve_member_upgrade(
    member_id: uuid.UUID,
    action: ApprovalAction,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Approve a pending tier upgrade for an already-approved member.
    Moves requested tiers into active tiers and clears the request flag.
    """
    query = (
        select(Member)
        .where(Member.id == member_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    if not member.membership or not member.membership.requested_tiers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No upgrade request pending for this member",
        )

    new_tiers = member.membership.requested_tiers or []
    member.membership.active_tiers = new_tiers
    if new_tiers:
        member.membership.primary_tier = new_tiers[0]

    member.membership.requested_tiers = None
    member.approved_by = current_user.email
    member.approved_at = datetime.now(timezone.utc)
    if action.notes:
        member.approval_notes = action.notes

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.post("/by-auth/{auth_id}/community/activate", response_model=MemberResponse)
async def admin_activate_community_membership_by_auth(
    auth_id: str,
    payload: ActivateCommunityRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply Community entitlement for a member (admin/service use)."""
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    now = datetime.now(timezone.utc)

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    base = (
        member.membership.community_paid_until
        if member.membership.community_paid_until
        and member.membership.community_paid_until > now
        else now
    )
    member.membership.community_paid_until = base + timedelta(days=365 * payload.years)

    if not member.membership.active_tiers:
        member.membership.active_tiers = ["community"]
    if not member.membership.primary_tier:
        member.membership.primary_tier = "community"

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.post("/by-auth/{auth_id}/community/extend", response_model=MemberResponse)
async def admin_extend_community_membership_by_auth(
    auth_id: str,
    payload: ExtendCommunityRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Extend Community membership by months (for stacking with Club)."""
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    now = datetime.now(timezone.utc)

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    # Extend from current expiry or from now if already expired
    base = (
        member.membership.community_paid_until
        if member.membership.community_paid_until
        and member.membership.community_paid_until > now
        else now
    )
    member.membership.community_paid_until = base + timedelta(days=30 * payload.months)

    if not member.membership.active_tiers:
        member.membership.active_tiers = ["community"]
    if not member.membership.primary_tier:
        member.membership.primary_tier = "community"

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()


@router.post("/by-auth/{auth_id}/club/activate", response_model=MemberResponse)
async def admin_activate_club_membership_by_auth(
    auth_id: str,
    payload: ActivateClubRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Apply Club entitlement for a member (admin/service use)."""
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    now = datetime.now(timezone.utc)

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    # Skip community check if explicitly requested (for bundle activations where community was just activated)
    if not payload.skip_community_check:
        if not (
            member.membership.community_paid_until
            and member.membership.community_paid_until > now
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Community membership is not active for this member",
            )

    approved_tiers = set(member.membership.active_tiers or [])
    requested_tiers = set(member.membership.requested_tiers or [])
    club_approved = "club" in approved_tiers or "academy" in approved_tiers
    club_requested = "club" in requested_tiers or "academy" in requested_tiers

    ec = member.emergency_contact
    av = member.availability
    readiness_complete = bool(
        ec
        and ec.name
        and ec.contact_relationship
        and ec.phone
        and av
        and av.preferred_locations
        and len(av.preferred_locations) > 0
        and av.preferred_times
        and len(av.preferred_times) > 0
        and av.available_days
        and len(av.available_days) > 0
    )

    if not club_approved:
        if not club_requested:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Club upgrade not requested",
            )
        if not readiness_complete:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Club readiness is incomplete",
            )

    tier_priority = {"academy": 3, "club": 2, "community": 1}

    base = (
        member.membership.club_paid_until
        if member.membership.club_paid_until and member.membership.club_paid_until > now
        else now
    )
    member.membership.club_paid_until = base + timedelta(days=30 * payload.months)

    updated_tiers = set(approved_tiers)
    updated_tiers.update({"club", "community"})

    if not club_approved:
        if member.membership.requested_tiers:
            remaining_requests = [
                tier
                for tier in member.membership.requested_tiers
                if tier not in {"club", "community"}
            ]
            member.membership.requested_tiers = remaining_requests or None
    elif member.membership.requested_tiers:
        remaining_requests = [
            tier
            for tier in member.membership.requested_tiers
            if tier not in {"club", "academy", "community"}
        ]
        member.membership.requested_tiers = remaining_requests or None

    sorted_tiers = sorted(
        [tier for tier in updated_tiers if tier in tier_priority],
        key=lambda tier: tier_priority[tier],
        reverse=True,
    )
    if sorted_tiers:
        member.membership.active_tiers = sorted_tiers
        current_priority = tier_priority.get(member.membership.primary_tier or "", 0)
        top_priority = tier_priority.get(sorted_tiers[0], 0)
        if top_priority > current_priority:
            member.membership.primary_tier = sorted_tiers[0]

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()


class MembershipPatchRequest(BaseModel):
    """Partial update for membership fields."""

    pending_payment_reference: str | None = None


@router.patch("/by-auth/{auth_id}/membership", response_model=MemberResponse)
async def admin_patch_membership_by_auth(
    auth_id: str,
    payload: MembershipPatchRequest,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Partially update membership fields for a member (admin/service use)."""
    query = (
        select(Member)
        .where(Member.auth_id == auth_id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Member not found"
        )

    if not member.membership:
        member.membership = MemberMembership(member_id=member.id)
        db.add(member.membership)

    # Update pending_payment_reference if provided (can be None to clear)
    if "pending_payment_reference" in payload.model_dump(exclude_unset=True):
        member.membership.pending_payment_reference = payload.pending_payment_reference

    db.add(member)
    await db.commit()
    await db.refresh(member)

    query = (
        select(Member)
        .where(Member.id == member.id)
        .options(*member_eager_load_options())
    )
    result = await db.execute(query)
    return result.scalar_one()
