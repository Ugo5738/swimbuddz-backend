import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import get_current_user
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.members_service.models import Member, PendingRegistration
from services.members_service.schemas import (
    MemberResponse, 
    MemberCreate, 
    PendingRegistrationCreate, 
    PendingRegistrationResponse
)

router = APIRouter(prefix="/members", tags=["members"])
pending_router = APIRouter(prefix="/pending-registrations", tags=["pending-registrations"])


@pending_router.post("/", response_model=PendingRegistrationResponse, status_code=status.HTTP_201_CREATED)
async def create_pending_registration(
    registration_in: PendingRegistrationCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a pending registration.
    This is called by the frontend before the user signs up with Supabase.
    """
    # Check if member already exists
    query = select(Member).where(Member.email == registration_in.email)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    # Check if pending registration already exists, update if so
    query = select(PendingRegistration).where(PendingRegistration.email == registration_in.email)
    result = await db.execute(query)
    pending = result.scalar_one_or_none()

    profile_data = registration_in.model_dump()
    profile_data_json = json.dumps(profile_data)

    if pending:
        pending.profile_data_json = profile_data_json
        # Update timestamp if we had one
    else:
        pending = PendingRegistration(
            email=registration_in.email,
            profile_data_json=profile_data_json
        )
        db.add(pending)

    await db.commit()
    await db.refresh(pending)
    return pending


@pending_router.post("/complete", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def complete_pending_registration(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Complete a pending registration.
    Called after the user has verified their email and is authenticated.
    """
    # Check if member already exists
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Member already exists",
        )

    # Find pending registration by email from token
    query = select(PendingRegistration).where(PendingRegistration.email == current_user.email)
    result = await db.execute(query)
    pending = result.scalar_one_or_none()

    if not pending:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending registration not found",
        )

    # Create member from pending data
    profile_data = json.loads(pending.profile_data_json)
    
    member = Member(
        auth_id=current_user.user_id,
        email=pending.email,
        first_name=profile_data.get("first_name"),
        last_name=profile_data.get("last_name"),
        registration_complete=True
    )
    
    db.add(member)
    await db.delete(pending)  # Cleanup pending
    await db.commit()
    await db.refresh(member)
    
    return member


@router.get("/me", response_model=MemberResponse)
async def get_current_member_profile(
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Get the profile of the currently authenticated member.
    """
    query = select(Member).where(Member.auth_id == current_user.user_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Please complete registration.",
        )

    return member


@router.post("/", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
async def create_member(
    member_in: MemberCreate,
    db: AsyncSession = Depends(get_async_db),
    # In a real app, we might restrict this to admin or internal services,
    # or ensure member_in.auth_id matches current_user.user_id
):
    """
    Directly create a member (internal use or admin).
    Normal users should go through the pending registration flow.
    """
    # Check if email already exists
    query = select(Member).where(Member.email == member_in.email)
    result = await db.execute(query)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )

    member = Member(**member_in.model_dump())
    db.add(member)
    await db.commit()
    await db.refresh(member)
    return member
