"""Router for volunteer and challenge management."""

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from libs.db.session import get_async_db
from services.members_service.models import (
    ClubChallenge,
    MemberChallengeCompletion,
    VolunteerInterest,
    VolunteerRole,
)
from services.members_service.schemas import (
    ChallengeCompletionCreate,
    ChallengeCompletionResponse,
    ClubChallengeCreate,
    ClubChallengeResponse,
    ClubChallengeUpdate,
    VolunteerInterestCreate,
    VolunteerInterestResponse,
    VolunteerRoleCreate,
    VolunteerRoleResponse,
    VolunteerRoleUpdate,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

# ===== VOLUNTEER ROLE ROUTER =====
volunteer_router = APIRouter(prefix="/volunteers", tags=["volunteers"])


@volunteer_router.get("/roles", response_model=List[VolunteerRoleResponse])
async def list_volunteer_roles(
    active_only: bool = Query(True, description="Show only active roles"),
    db: AsyncSession = Depends(get_async_db),
):
    """List volunteer roles with optional filters."""
    query = select(VolunteerRole)

    if active_only:
        query = query.where(VolunteerRole.is_active.is_(True))

    query = query.order_by(VolunteerRole.created_at.desc())

    result = await db.execute(query)
    roles = result.scalars().all()

    # Get interested member counts for each role
    roles_with_counts = []
    for role in roles:
        interest_query = select(func.count(VolunteerInterest.id)).where(
            VolunteerInterest.role_id == role.id
        )
        interest_result = await db.execute(interest_query)
        interested_count = interest_result.scalar_one()

        role_dict = role.__dict__.copy()
        role_dict["interested_count"] = interested_count
        roles_with_counts.append(VolunteerRoleResponse.model_validate(role_dict))

    return roles_with_counts


@volunteer_router.get("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def get_volunteer_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single volunteer role by ID."""
    query = select(VolunteerRole).where(VolunteerRole.id == role_id)
    result = await db.execute(query)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Get interested count
    interest_query = select(func.count(VolunteerInterest.id)).where(
        VolunteerInterest.role_id == role.id
    )
    interest_result = await db.execute(interest_query)
    interested_count = interest_result.scalar_one()

    role_dict = role.__dict__.copy()
    role_dict["interested_count"] = interested_count

    return VolunteerRoleResponse.model_validate(role_dict)


@volunteer_router.post("/roles", response_model=VolunteerRoleResponse, status_code=201)
async def create_volunteer_role(
    role_data: VolunteerRoleCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new volunteer role (admin only)."""
    role = VolunteerRole(**role_data.model_dump())

    db.add(role)
    await db.commit()
    await db.refresh(role)

    role_dict = role.__dict__.copy()
    role_dict["interested_count"] = 0

    return VolunteerRoleResponse.model_validate(role_dict)


@volunteer_router.patch("/roles/{role_id}", response_model=VolunteerRoleResponse)
async def update_volunteer_role(
    role_id: uuid.UUID,
    role_data: VolunteerRoleUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update a volunteer role (admin only)."""
    query = select(VolunteerRole).where(VolunteerRole.id == role_id)
    result = await db.execute(query)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Update only provided fields
    for field, value in role_data.model_dump(exclude_unset=True).items():
        setattr(role, field, value)

    await db.commit()
    await db.refresh(role)

    # Get interested count
    interest_query = select(func.count(VolunteerInterest.id)).where(
        VolunteerInterest.role_id == role.id
    )
    interest_result = await db.execute(interest_query)
    interested_count = interest_result.scalar_one()

    role_dict = role.__dict__.copy()
    role_dict["interested_count"] = interested_count

    return VolunteerRoleResponse.model_validate(role_dict)


@volunteer_router.delete("/roles/{role_id}", status_code=204)
async def delete_volunteer_role(
    role_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a volunteer role (admin only)."""
    query = select(VolunteerRole).where(VolunteerRole.id == role_id)
    result = await db.execute(query)
    role = result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Delete associated interests first
    await db.execute(
        select(VolunteerInterest).where(VolunteerInterest.role_id == role_id)
    )
    await db.delete(role)
    await db.commit()

    return None


# ===== VOLUNTEER INTEREST ENDPOINTS =====
@volunteer_router.post(
    "/interest", response_model=VolunteerInterestResponse, status_code=201
)
async def register_volunteer_interest(
    interest_data: VolunteerInterestCreate,
    # TODO: Get member_id from authentication
    member_id: uuid.UUID = Query(..., description="Member ID"),
    db: AsyncSession = Depends(get_async_db),
):
    """Register interest in a volunteer role."""
    # Check if role exists
    role_query = select(VolunteerRole).where(VolunteerRole.id == interest_data.role_id)
    role_result = await db.execute(role_query)
    role = role_result.scalar_one_or_none()

    if not role:
        raise HTTPException(status_code=404, detail="Volunteer role not found")

    # Check if already interested
    existing_query = select(VolunteerInterest).where(
        VolunteerInterest.role_id == interest_data.role_id,
        VolunteerInterest.member_id == member_id,
    )
    existing_result = await db.execute(existing_query)
    existing_interest = existing_result.scalar_one_or_none()

    if existing_interest:
        raise HTTPException(
            status_code=400, detail="Already registered interest in this role"
        )

    interest = VolunteerInterest(
        role_id=interest_data.role_id, member_id=member_id, notes=interest_data.notes
    )

    db.add(interest)
    await db.commit()
    await db.refresh(interest)

    return VolunteerInterestResponse.model_validate(interest)


@volunteer_router.get(
    "/roles/{role_id}/interested", response_model=List[VolunteerInterestResponse]
)
async def list_interested_members(
    role_id: uuid.UUID,
    status: Optional[str] = Query(None, description="Filter by status"),
    db: AsyncSession = Depends(get_async_db),
):
    """List members interested in a volunteer role (admin only)."""
    query = select(VolunteerInterest).where(VolunteerInterest.role_id == role_id)

    if status:
        query = query.where(VolunteerInterest.status == status)

    result = await db.execute(query)
    interests = result.scalars().all()

    return [
        VolunteerInterestResponse.model_validate(interest) for interest in interests
    ]


# ===== CLUB CHALLENGE ROUTER =====
challenge_router = APIRouter(prefix="/challenges", tags=["challenges"])


@challenge_router.get("/", response_model=List[ClubChallengeResponse])
async def list_club_challenges(
    active_only: bool = Query(True, description="Show only active challenges"),
    challenge_type: Optional[str] = Query(None, description="Filter by challenge type"),
    db: AsyncSession = Depends(get_async_db),
):
    """List club challenges with optional filters."""
    query = select(ClubChallenge)

    if active_only:
        query = query.where(ClubChallenge.is_active.is_(True))

    if challenge_type:
        query = query.where(ClubChallenge.challenge_type == challenge_type)

    query = query.order_by(ClubChallenge.created_at.desc())

    result = await db.execute(query)
    challenges = result.scalars().all()

    # Get completion counts for each challenge
    challenges_with_counts = []
    for challenge in challenges:
        completion_query = select(func.count(MemberChallengeCompletion.id)).where(
            MemberChallengeCompletion.challenge_id == challenge.id
        )
        completion_result = await db.execute(completion_query)
        completion_count = completion_result.scalar_one()

        challenge_dict = challenge.__dict__.copy()
        challenge_dict["completion_count"] = completion_count
        challenges_with_counts.append(
            ClubChallengeResponse.model_validate(challenge_dict)
        )

    return challenges_with_counts


@challenge_router.get("/{challenge_id}", response_model=ClubChallengeResponse)
async def get_club_challenge(
    challenge_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Get a single club challenge by ID."""
    query = select(ClubChallenge).where(ClubChallenge.id == challenge_id)
    result = await db.execute(query)
    challenge = result.scalar_one_or_none()

    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")

    # Get completion count
    completion_query = select(func.count(MemberChallengeCompletion.id)).where(
        MemberChallengeCompletion.challenge_id == challenge.id
    )
    completion_result = await db.execute(completion_query)
    completion_count = completion_result.scalar_one()

    challenge_dict = challenge.__dict__.copy()
    challenge_dict["completion_count"] = completion_count

    return ClubChallengeResponse.model_validate(challenge_dict)


@challenge_router.post("/", response_model=ClubChallengeResponse, status_code=201)
async def create_club_challenge(
    challenge_data: ClubChallengeCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """Create a new club challenge (admin only)."""
    import json

    challenge = ClubChallenge(
        **challenge_data.model_dump(exclude={"criteria_json"}),
        criteria_json=(
            json.dumps(challenge_data.criteria_json)
            if challenge_data.criteria_json
            else None
        ),
    )

    db.add(challenge)
    await db.commit()
    await db.refresh(challenge)

    challenge_dict = challenge.__dict__.copy()
    challenge_dict["completion_count"] = 0
    challenge_dict["criteria_json"] = (
        json.loads(challenge.criteria_json) if challenge.criteria_json else None
    )

    return ClubChallengeResponse.model_validate(challenge_dict)


@challenge_router.patch("/{challenge_id}", response_model=ClubChallengeResponse)
async def update_club_challenge(
    challenge_id: uuid.UUID,
    challenge_data: ClubChallengeUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    """Update a club challenge (admin only)."""
    import json

    query = select(ClubChallenge).where(ClubChallenge.id == challenge_id)
    result = await db.execute(query)
    challenge = result.scalar_one_or_none()

    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")

    # Update only provided fields
    update_data = challenge_data.model_dump(
        exclude_unset=True, exclude={"criteria_json"}
    )
    for field, value in update_data.items():
        setattr(challenge, field, value)

    if "criteria_json" in challenge_data.model_dump(exclude_unset=True):
        challenge.criteria_json = (
            json.dumps(challenge_data.criteria_json)
            if challenge_data.criteria_json
            else None
        )

    await db.commit()
    await db.refresh(challenge)

    # Get completion count
    completion_query = select(func.count(MemberChallengeCompletion.id)).where(
        MemberChallengeCompletion.challenge_id == challenge.id
    )
    completion_result = await db.execute(completion_query)
    completion_count = completion_result.scalar_one()

    challenge_dict = challenge.__dict__.copy()
    challenge_dict["completion_count"] = completion_count
    challenge_dict["criteria_json"] = (
        json.loads(challenge.criteria_json) if challenge.criteria_json else None
    )

    return ClubChallengeResponse.model_validate(challenge_dict)


@challenge_router.delete("/{challenge_id}", status_code=204)
async def delete_club_challenge(
    challenge_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """Delete a club challenge (admin only)."""
    query = select(ClubChallenge).where(ClubChallenge.id == challenge_id)
    result = await db.execute(query)
    challenge = result.scalar_one_or_none()

    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")

    # Delete associated completions first
    await db.execute(
        select(MemberChallengeCompletion).where(
            MemberChallengeCompletion.challenge_id == challenge_id
        )
    )
    await db.delete(challenge)
    await db.commit()

    return None


# ===== CHALLENGE COMPLETION ENDPOINTS =====
@challenge_router.post(
    "/completions", response_model=ChallengeCompletionResponse, status_code=201
)
async def mark_challenge_complete(
    completion_data: ChallengeCompletionCreate,
    # TODO: Get verified_by from authentication (admin/coach)
    verified_by: Optional[uuid.UUID] = Query(
        None, description="Admin/coach verifying completion"
    ),
    db: AsyncSession = Depends(get_async_db),
):
    """Mark a challenge as complete for a member (admin/coach only)."""
    import json

    # Verify challenge exists
    challenge_query = select(ClubChallenge).where(
        ClubChallenge.id == completion_data.challenge_id
    )
    challenge_result = await db.execute(challenge_query)
    challenge = challenge_result.scalar_one_or_none()

    if not challenge:
        raise HTTPException(status_code=404, detail="Club challenge not found")

    # Check if already completed
    existing_query = select(MemberChallengeCompletion).where(
        MemberChallengeCompletion.challenge_id == completion_data.challenge_id,
        MemberChallengeCompletion.member_id == completion_data.member_id,
    )
    existing_result = await db.execute(existing_query)
    existing_completion = existing_result.scalar_one_or_none()

    if existing_completion:
        raise HTTPException(
            status_code=400, detail="Challenge already completed by this member"
        )

    completion = MemberChallengeCompletion(
        challenge_id=completion_data.challenge_id,
        member_id=completion_data.member_id,
        result_data=(
            json.dumps(completion_data.result_data)
            if completion_data.result_data
            else None
        ),
        verified_by=verified_by,
    )

    db.add(completion)
    await db.commit()
    await db.refresh(completion)

    completion_dict = completion.__dict__.copy()
    completion_dict["result_data"] = (
        json.loads(completion.result_data) if completion.result_data else None
    )

    return ChallengeCompletionResponse.model_validate(completion_dict)


@challenge_router.get(
    "/{challenge_id}/completions", response_model=List[ChallengeCompletionResponse]
)
async def list_challenge_completions(
    challenge_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    """List all completions for a specific challenge (admin only)."""
    import json

    query = (
        select(MemberChallengeCompletion)
        .where(MemberChallengeCompletion.challenge_id == challenge_id)
        .order_by(MemberChallengeCompletion.completed_at.desc())
    )

    result = await db.execute(query)
    completions = result.scalars().all()

    completions_list = []
    for completion in completions:
        completion_dict = completion.__dict__.copy()
        completion_dict["result_data"] = (
            json.loads(completion.result_data) if completion.result_data else None
        )
        completions_list.append(
            ChallengeCompletionResponse.model_validate(completion_dict)
        )

    return completions_list
