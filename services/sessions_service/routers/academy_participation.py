"""Admin booking of a learner's individually classified Academy class.

Physical session is shared; the agreed charge and class consumption are per
enrollment. Always reserve entitlement before recording attendance.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import get_member_by_id, internal_post
from libs.db.session import get_async_db
from services.sessions_service.models import (
    BookingChannel,
    Session,
    SessionBooking,
    SessionBookingStatus,
    SessionType,
)
from services.sessions_service.services.booking_capacity import assert_booking_capacity
from services.sessions_service.routers.bookings import _record_walk_in_attendance

router = APIRouter(tags=["academy-session-participation"])


class AcademyClassAdminBooking(BaseModel):
    member_id: uuid.UUID
    enrollment_id: uuid.UUID
    kind: str = Field(pattern="^(prepaid|paid_extra)$")
    agreed_fee_kobo: int = Field(default=0, ge=0)
    reason: str = Field(min_length=10, max_length=1000)


@router.post("/sessions/{session_id}/admin/academy-participation")
async def record_academy_participation(
    session_id: uuid.UUID,
    payload: AcademyClassAdminBooking,
    actor: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    session = (
        await db.execute(select(Session).where(Session.id == session_id))
    ).scalar_one_or_none()
    if session is None:
        raise HTTPException(404, "Session not found")
    if session.session_type != SessionType.COHORT_CLASS or not session.cohort_id:
        raise HTTPException(422, "Only a real cohort class may use Academy allocation")
    if payload.kind == "prepaid" and payload.agreed_fee_kobo:
        raise HTTPException(422, "Prepaid lessons must have zero additional charge")
    if payload.kind == "paid_extra" and payload.agreed_fee_kobo <= 0:
        raise HTTPException(422, "Additional classes need a positive agreed fee")

    existing = (
        await db.execute(
            select(SessionBooking).where(
                SessionBooking.session_id == session_id,
                SessionBooking.member_id == payload.member_id,
            )
        )
    ).scalar_one_or_none()
    if existing and (
        existing.booking_source != "academy_entitlement"
        or existing.status != SessionBookingStatus.CONFIRMED
        or existing.fee_amount_kobo != payload.agreed_fee_kobo
        or existing.rate_code != payload.kind
    ):
        raise HTTPException(
            409,
            "Existing session booking has different terms. Manual financial review required.",
        )
    member = await get_member_by_id(str(payload.member_id), calling_service="sessions")
    if not member or not member.get("auth_id"):
        raise HTTPException(404, "Registered member profile not found")

    if existing is None:
        await assert_booking_capacity(
            db, session=session, member_id=payload.member_id, new_party_size=1
        )

    try:
        response = await internal_post(
            service_url=get_settings().ACADEMY_SERVICE_URL,
            path=f"/academy/admin/enrollments/{payload.enrollment_id}/class-entitlements/allocations",
            calling_service="sessions",
            json={
                "session_id": str(session_id),
                "member_id": str(payload.member_id),
                "kind": payload.kind,
                "agreed_fee_kobo": payload.agreed_fee_kobo,
                "reason": payload.reason,
            },
        )
    except Exception as exc:
        raise HTTPException(
            503, "Class entitlement service unavailable; booking unchanged"
        ) from exc
    if response.status_code >= 400:
        raise HTTPException(response.status_code, response.text)
    reservation = response.json()
    if existing is None:
        now = utc_now()
        existing = SessionBooking(
            session_id=session_id,
            member_id=payload.member_id,
            member_auth_id=member["auth_id"],
            status=SessionBookingStatus.CONFIRMED,
            channel=BookingChannel.ADMIN,
            party_size=1,
            fee_amount_kobo=payload.agreed_fee_kobo,
            member_fee_amount_kobo=payload.agreed_fee_kobo,
            access_source="academy_allocation",
            pricing_audience="academy",
            pricing_source="admin_override",
            rate_code=payload.kind,
            booking_source="academy_entitlement",
            notes=payload.reason[:500],
            booked_at=now,
            confirmed_at=now,
        )
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
    try:
        confirmed = await internal_post(
            service_url=get_settings().ACADEMY_SERVICE_URL,
            path=(
                f"/academy/internal/enrollments/{payload.enrollment_id}"
                f"/class-allocations/{reservation['id']}/apply"
            ),
            calling_service="sessions",
            json={"booking_id": str(existing.id)},
        )
        confirmed.raise_for_status()
    except Exception as exc:
        raise HTTPException(
            503,
            "Booking saved, but class allocation confirmation needs retry; no additional charge collected.",
        ) from exc
    await _record_walk_in_attendance(session_id, payload.member_id)
    return {
        "session_id": str(session_id),
        "member_id": str(payload.member_id),
        "booking_id": str(existing.id),
        "allocation_id": reservation["id"],
        "kind": payload.kind,
        "fee_amount_kobo": existing.fee_amount_kobo,
        "payment_status": "outstanding" if existing.fee_amount_kobo else "included",
        "entitlement_status": "applied",
    }
