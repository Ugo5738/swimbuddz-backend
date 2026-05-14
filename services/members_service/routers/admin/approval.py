"""Pending-member listing, lookup, and approve/reject/upgrade flows."""

import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.emails.client import get_email_client
from libs.db.session import get_async_db
from services.members_service.models import Member
from services.members_service.routers._helpers import member_eager_load_options
from services.members_service.schemas import (
    ApprovalAction,
    MemberResponse,
    PendingMemberResponse,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


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
