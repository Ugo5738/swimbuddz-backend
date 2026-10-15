"""Admin-approved per-enrollment Club commercial terms.

Only future, unquoted regular transition practices are affected. Never mutate
a paid booking, provider payment, or published Club price.
"""
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.members_service.models import ClubCommercialAdjustment, ClubEnrollment

router = APIRouter(prefix="/clubs/admin", tags=["clubs-admin-commercial-terms"])


class ClubTermsApproval(BaseModel):
    adjustment_id: UUID
    agreed_session_fee_kobo: int = Field(ge=0)
    standard_session_fee_kobo: int = Field(gt=0)
    valid_from: datetime
    valid_until: datetime
    reason: str = Field(min_length=20, max_length=2000)

    @model_validator(mode="after")
    def check_terms(self):
        if self.valid_from.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("Commercial term dates must include a timezone")
        if self.valid_until <= self.valid_from:
            raise ValueError("Commercial terms must have a positive duration")
        if self.agreed_session_fee_kobo > self.standard_session_fee_kobo:
            raise ValueError("Agreed fee cannot exceed the reference Club fee")
        return self


async def _enrollment(db: AsyncSession, enrollment_id: UUID):
    row = await db.get(ClubEnrollment, enrollment_id)
    if row is None:
        raise HTTPException(404, "Club enrollment not found")
    return row


@router.get("/enrollments/{enrollment_id}/commercial-terms")
async def list_club_terms(
    enrollment_id: UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    enrollment = await _enrollment(db, enrollment_id)
    rows = (await db.execute(
        select(ClubCommercialAdjustment)
        .where(ClubCommercialAdjustment.enrollment_id == enrollment.id)
        .order_by(ClubCommercialAdjustment.created_at.desc())
    )).scalars().all()
    return {
        "enrollment_id": str(enrollment.id),
        "payment_mode": enrollment.payment_mode,
        "ends_at": enrollment.ends_at,
        "adjustments": [
            {
                "id": str(row.id),
                "agreed_session_fee_kobo": row.session_fee_kobo,
                "valid_from": row.valid_from,
                "valid_until": row.valid_until,
                "reason": row.reason,
                "actor_auth_id": row.actor_auth_id,
                "original_terms": row.original_terms,
                "approved_terms": row.approved_terms,
            } for row in rows
        ],
    }


@router.post("/enrollments/{enrollment_id}/commercial-terms")
async def approve_club_terms(
    enrollment_id: UUID,
    body: ClubTermsApproval,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    existing = await db.get(ClubCommercialAdjustment, body.adjustment_id)
    expected = body.model_dump(mode="json")
    if existing is not None:
        if existing.enrollment_id != enrollment_id or existing.approved_terms.get("request") != expected:
            raise HTTPException(409, "Adjustment ID already used for different terms")
        return {"adjustment_id": str(existing.id), "idempotent": True}

    enrollment = (await db.execute(
        select(ClubEnrollment).where(ClubEnrollment.id == enrollment_id).with_for_update()
    )).scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(404, "Club enrollment not found")
    if enrollment.payment_mode != "transition_per_session" or enrollment.status != "active":
        raise HTTPException(409, "Only active pay-per-session Club enrollments can be amended")
    now = utc_now()
    start = body.valid_from.astimezone(timezone.utc)
    end = body.valid_until.astimezone(timezone.utc)
    if start < now:
        raise HTTPException(422, "Commercial terms can only start from the present onward")
    if start < enrollment.starts_at or end > enrollment.ends_at:
        raise HTTPException(409, "Agreement dates must fit the active Club enrollment period")
    if (await db.execute(
        select(ClubCommercialAdjustment.id).where(
            ClubCommercialAdjustment.enrollment_id == enrollment.id,
            ClubCommercialAdjustment.valid_from < end,
            ClubCommercialAdjustment.valid_until > start,
        )
    )).first() is not None:
        raise HTTPException(409, "Another approved agreement overlaps these dates; resolve it first")
    row = ClubCommercialAdjustment(
        id=body.adjustment_id,
        enrollment_id=enrollment.id,
        actor_auth_id=admin.user_id,
        reason=body.reason,
        original_terms={"standard_session_fee_kobo": body.standard_session_fee_kobo},
        approved_terms={"request": expected, "agreed_session_fee_kobo": body.agreed_session_fee_kobo},
        session_fee_kobo=body.agreed_session_fee_kobo,
        valid_from=start,
        valid_until=end,
    )
    db.add(row)
    await db.commit()
    return {"adjustment_id": str(row.id), "idempotent": False}


@router.get("/commercial-terms/report")
async def club_commercial_terms_report(
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Approved Club concessions, not inferred future billing or ledger revenue.

    A transition member owes nothing for an unbooked swim. Only an actual
    booking freezes the agreed rate into an obligation/payment attempt.
    """
    from services.members_service.models import Club
    rows = (await db.execute(
        select(ClubCommercialAdjustment, ClubEnrollment, Club)
        .join(ClubEnrollment, ClubEnrollment.id == ClubCommercialAdjustment.enrollment_id)
        .join(Club, Club.id == ClubEnrollment.club_id)
        .order_by(ClubCommercialAdjustment.created_at.desc())
    )).all()
    items = []
    for adjustment, enrollment, club in rows:
        baseline = adjustment.original_terms.get("standard_session_fee_kobo")
        agreed = adjustment.session_fee_kobo
        items.append({
            "adjustment_id": str(adjustment.id),
            "member_id": str(enrollment.member_id),
            "enrollment_id": str(enrollment.id),
            "club_name": club.name,
            "standard_session_fee_kobo": baseline,
            "agreed_session_fee_kobo": agreed,
            "potential_concession_per_swim_kobo": (
                max(0, int(baseline) - agreed) if baseline is not None else None
            ),
            "valid_from": adjustment.valid_from,
            "valid_until": adjustment.valid_until,
            "reason": adjustment.reason,
            "approved_by": adjustment.actor_auth_id,
        })
    return {
        "items": items,
        "count": len(items),
        "note": (
            "This is the approved commercial terms register. It does not imply "
            "that every future swim has been booked or billed. Realized concession "
            "and receivables must be derived from immutable SessionBooking quotes "
            "and actual payment settlement; accounting income remains Ledger-owned."
        ),
    }
