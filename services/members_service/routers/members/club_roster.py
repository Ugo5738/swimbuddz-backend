"""Admin expected-roster lookup; never records attendance or payment."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.db.session import get_async_db
from services.members_service.schemas.club_roster import (
    ClubSessionRosterContext,
    ClubSessionRosterResponse,
)
from services.members_service.services.club_roster import club_session_roster

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/club-session-roster", response_model=ClubSessionRosterResponse)
async def get_club_session_roster(
    context: ClubSessionRosterContext = Depends(),
    db: AsyncSession = Depends(get_async_db),
) -> ClubSessionRosterResponse:
    return ClubSessionRosterResponse(
        session_id=context.session_id,
        members=await club_session_roster(db, context),
    )
