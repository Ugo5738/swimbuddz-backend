"""The session context used to build an Admin's expected Club roster."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel


class ClubSessionRosterContext(BaseModel):
    session_id: UUID
    at: AwareDatetime
    club_id: UUID | None = None
    pool_id: UUID | None = None
    pod_id: UUID | None = None
    club_access_mode: Literal["plan_included", "active_club", "paid_addon"] = (
        "plan_included"
    )
    allows_visiting_club_members: bool = False


class ClubSessionRosterMember(BaseModel):
    member_id: UUID
    name: str
    email: str
    access_source: str


class ClubSessionRosterResponse(BaseModel):
    session_id: UUID
    members: list[ClubSessionRosterMember]
