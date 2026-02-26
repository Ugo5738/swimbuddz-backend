from fastapi import APIRouter
from services.academy_service.routers._shared import *  # noqa: F401, F403

router = APIRouter(tags=["academy"])
logger = get_logger(__name__)


# --- Programs ---


@router.post("/programs", response_model=ProgramResponse)
async def create_program(
    program_in: ProgramCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    program = Program(**program_in.model_dump())
    db.add(program)
    await db.commit()
    await db.refresh(program)
    return program


@router.get("/programs", response_model=List[ProgramResponse])
async def list_programs(
    published_only: bool = False,
    db: AsyncSession = Depends(get_async_db),
):
    """List all programs. Use published_only=true for member-facing views."""
    query = select(Program).order_by(Program.name)
    if published_only:
        query = query.where(Program.is_published.is_(True))
    result = await db.execute(query)
    programs = result.scalars().all()

    # Resolve cover image URLs
    media_ids = [p.cover_image_media_id for p in programs if p.cover_image_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses = []
    for program in programs:
        resp = ProgramResponse.model_validate(program)
        if program.cover_image_media_id:
            resp.cover_image_url = url_map.get(program.cover_image_media_id)
        responses.append(resp)
    return responses


@router.get("/programs/published", response_model=List[ProgramResponse])
async def list_published_programs(
    db: AsyncSession = Depends(get_async_db),
):
    """List only published programs (for member-facing pages)."""
    query = select(Program).where(Program.is_published.is_(True)).order_by(Program.name)
    result = await db.execute(query)
    programs = result.scalars().all()

    # Resolve cover image URLs
    media_ids = [p.cover_image_media_id for p in programs if p.cover_image_media_id]
    url_map = await resolve_media_urls(media_ids) if media_ids else {}

    responses = []
    for program in programs:
        resp = ProgramResponse.model_validate(program)
        if program.cover_image_media_id:
            resp.cover_image_url = url_map.get(program.cover_image_media_id)
        responses.append(resp)
    return responses


@router.get("/programs/{program_id}", response_model=ProgramResponse)
async def get_program(
    program_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Program).where(Program.id == program_id)
    result = await db.execute(query)
    program = result.scalar_one_or_none()
    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    # Resolve cover image URL
    resp = ProgramResponse.model_validate(program)
    resp.cover_image_url = await resolve_media_url(program.cover_image_media_id)
    return resp


@router.put("/programs/{program_id}", response_model=ProgramResponse)
async def update_program(
    program_id: uuid.UUID,
    program_in: ProgramUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Program).where(Program.id == program_id)
    result = await db.execute(query)
    program = result.scalar_one_or_none()

    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    update_data = program_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(program, field, value)

    await db.commit()
    await db.refresh(program)

    # Resolve cover image URL
    resp = ProgramResponse.model_validate(program)
    resp.cover_image_url = await resolve_media_url(program.cover_image_media_id)
    return resp


@router.delete("/programs/{program_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_program(
    program_id: uuid.UUID,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(Program).where(Program.id == program_id)
    result = await db.execute(query)
    program = result.scalar_one_or_none()

    if not program:
        raise HTTPException(status_code=404, detail="Program not found")

    await db.delete(program)
    await db.commit()
    return None


# --- Milestones ---


@router.post("/milestones", response_model=MilestoneResponse)
async def create_milestone(
    milestone_in: MilestoneCreate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    milestone = Milestone(**milestone_in.model_dump())
    db.add(milestone)
    await db.commit()
    await db.refresh(milestone)
    return milestone


@router.get("/programs/{program_id}/milestones", response_model=List[MilestoneResponse])
async def list_program_milestones(
    program_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    query = (
        select(Milestone)
        .where(Milestone.program_id == program_id)
        .order_by(Milestone.order_index)
    )
    result = await db.execute(query)
    return result.scalars().all()


# --- Program Interest (Get Notified) ---


@router.post("/programs/{program_id}/interest")
async def register_program_interest(
    program_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Register interest in a program to be notified when new cohorts open.
    """
    # Verify program exists
    program_query = select(Program).where(Program.id == program_id)
    program_result = await db.execute(program_query)
    program = program_result.scalar_one_or_none()

    if not program:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Program not found",
        )

    # Get member info via members service
    member = await get_member_by_auth_id(
        current_user.user_id, calling_service="academy"
    )

    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )

    # Check if interest already exists
    existing_query = select(ProgramInterest).where(
        ProgramInterest.program_id == program_id,
        ProgramInterest.member_auth_id == current_user.user_id,
    )
    existing_result = await db.execute(existing_query)
    existing = existing_result.scalar_one_or_none()

    if existing:
        return {
            "message": "You're already registered to receive notifications for this program",
            "registered": True,
        }

    # Create interest record
    interest = ProgramInterest(
        program_id=program_id,
        member_id=member["id"],
        member_auth_id=current_user.user_id,
        email=member.get("email"),
    )
    db.add(interest)
    await db.commit()

    return {
        "message": f"Great! You'll be notified when new cohorts for '{program.name}' open.",
        "registered": True,
    }


@router.delete("/programs/{program_id}/interest")
async def remove_program_interest(
    program_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Remove interest in a program (unsubscribe from notifications).
    """
    query = select(ProgramInterest).where(
        ProgramInterest.program_id == program_id,
        ProgramInterest.member_auth_id == current_user.user_id,
    )
    result = await db.execute(query)
    interest = result.scalar_one_or_none()

    if not interest:
        return {"message": "Not registered for notifications", "registered": False}

    await db.delete(interest)
    await db.commit()

    return {
        "message": "You've been unsubscribed from notifications",
        "registered": False,
    }


@router.get("/programs/{program_id}/interest")
async def check_program_interest(
    program_id: uuid.UUID,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Check if the current user is registered for notifications for a program.
    """
    query = select(ProgramInterest).where(
        ProgramInterest.program_id == program_id,
        ProgramInterest.member_auth_id == current_user.user_id,
    )
    result = await db.execute(query)
    interest = result.scalar_one_or_none()

    return {"registered": interest is not None}
