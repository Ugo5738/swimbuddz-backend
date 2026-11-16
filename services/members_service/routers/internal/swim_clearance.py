"""Safety-only Academy clearance. Never disclose emergency/medical details."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, load_only, raiseload

from libs.auth.dependencies import require_service_role
from libs.db.session import get_async_db
from services.members_service.models import (
    Member,
    MemberEmergencyContact,
    MemberProfile,
)

router = APIRouter(tags=["internal-academy-clearance"])


class ClearanceBatchRequest(BaseModel):
    member_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)


def clearance_query(member_ids: list[uuid.UUID]):
    """One safety-only read; never hydrate membership, coaching or medical data."""
    return (
        select(Member)
        .where(Member.id.in_(member_ids))
        .options(
            load_only(Member.id, Member.is_active, raiseload=True),
            raiseload("*"),
            joinedload(Member.profile).load_only(
                MemberProfile.phone,
                MemberProfile.swim_level,
                MemberProfile.deep_water_comfort,
                raiseload=True,
            ),
            joinedload(Member.emergency_contact).load_only(
                MemberEmergencyContact.name,
                MemberEmergencyContact.phone,
                MemberEmergencyContact.contact_relationship,
                raiseload=True,
            ),
        )
    )


def missing_requirements(member: Member | None) -> list[str]:
    if member is None or not member.is_active:
        return ["member_account"]
    profile = member.profile
    emergency = member.emergency_contact
    missing = []
    if not profile or not profile.phone:
        missing.append("contact_phone")
    if not profile or not profile.swim_level or not profile.deep_water_comfort:
        missing.append("swimming_background")
    if (
        not emergency
        or not emergency.name
        or not emergency.phone
        or not emergency.contact_relationship
    ):
        missing.append("emergency_contact")
    return missing


@router.get("/academy-swim-clearance/{member_id}")
async def academy_clearance(
    member_id: uuid.UUID,
    _service=Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    member = (await db.execute(clearance_query([member_id]))).scalar_one_or_none()
    missing = missing_requirements(member)
    return {"member_id": str(member_id), "ready": not missing, "missing": missing}


@router.post("/academy-swim-clearance/batch")
async def academy_clearance_batch(
    body: ClearanceBatchRequest,
    _service=Depends(require_service_role),
    db: AsyncSession = Depends(get_async_db),
):
    members = (await db.execute(clearance_query(body.member_ids))).scalars().all()
    mapping = {member.id: member for member in members}
    return {
        str(member_id): {
            "ready": not (missing := missing_requirements(mapping.get(member_id))),
            "missing": missing,
        }
        for member_id in body.member_ids
    }
