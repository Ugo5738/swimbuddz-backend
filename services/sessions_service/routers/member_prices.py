"""Read-only prices from the booking resolver, including frozen bookings."""

from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_service_role
from libs.common.datetime_utils import utc_now
from libs.common.service_client import check_club_access_batch
from libs.common.session_access import evaluate_session_access
from libs.db.session import get_async_db
from services.sessions_service.models import Session, SessionBooking, SessionRate
from services.sessions_service.schemas.member_prices import (
    MemberPriceResponse,
    MemberPricesRequest,
    MemberPricesResponse,
)
from services.sessions_service.services.commercial import apply_explicit_rate
from services.sessions_service.services.session_access import _club_access_check

router = APIRouter(dependencies=[Depends(require_service_role)])


@router.post("/member-prices", response_model=MemberPricesResponse)
async def member_prices(
    payload: MemberPricesRequest,
    db: AsyncSession = Depends(get_async_db),
) -> MemberPricesResponse:
    now = utc_now()
    session_ids = {check.session_id for check in payload.checks}
    member_ids = {check.member_id for check in payload.checks}
    sessions = {
        row.id: row
        for row in (
            await db.execute(select(Session).where(Session.id.in_(session_ids)))
        ).scalars()
    }
    bookings = {
        (row.session_id, row.member_id): row
        for row in (
            await db.execute(
                select(SessionBooking).where(
                    SessionBooking.session_id.in_(session_ids),
                    SessionBooking.member_id.in_(member_ids),
                    or_(
                        SessionBooking.status == "confirmed",
                        and_(
                            SessionBooking.status == "pending",
                            or_(
                                SessionBooking.expires_at.is_(None),
                                SessionBooking.expires_at > now,
                            ),
                        ),
                    ),
                )
            )
        ).scalars()
    }
    rates: dict = {}
    for row in (
        await db.execute(
            select(SessionRate).where(
                SessionRate.session_id.in_(session_ids),
                SessionRate.is_active.is_(True),
            )
        )
    ).scalars():
        rates.setdefault(row.session_id, []).append(row)
    club_checks = []
    for check in payload.checks:
        session = sessions.get(check.session_id)
        if (
            session
            and session.session_type == "club"
            and (check.session_id, check.member_id) not in bookings
        ):
            club_checks.append(
                {
                    **_club_access_check(session, str(check.member_id)),
                    "context_key": check.context_key,
                }
            )
    access = await check_club_access_batch(club_checks, calling_service="sessions")
    items = []
    for check in payload.checks:
        booking = bookings.get((check.session_id, check.member_id))
        session = sessions.get(check.session_id)
        if booking is not None:
            item = MemberPriceResponse(
                context_key=check.context_key,
                fee_amount_kobo=booking.fee_amount_kobo,
                pricing_source="booking",
                price_label="Saved booking fee",
            )
        elif session and check.context_key in access:
            decision = evaluate_session_access(
                {"member_id": str(check.member_id)},
                session,
                now=now,
                club_access_result=access[check.context_key],
            )
            decision = apply_explicit_rate(decision, rates.get(session.id, []))
            item = MemberPriceResponse(
                context_key=check.context_key,
                fee_amount_kobo=decision.fee_amount_kobo,
                pricing_source=decision.pricing_source,
                price_label=decision.price_label,
            )
        else:
            item = MemberPriceResponse(context_key=check.context_key)
        items.append(item)
    return MemberPricesResponse(items=items)
