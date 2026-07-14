"""POST /payments/intents — kick off a payment for a member.

Branches on `PaymentPurpose` to compute the correct amount (and any
community-extension top-ups for Club purchases), applies discounts +
Bubbles, persists a PENDING Payment row, and (for the paystack
method) initializes the Paystack checkout and returns the
authorization URL.
"""

import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import _service_role_jwt, get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.currency import (
    KOBO_PER_BUBBLE,
    KOBO_PER_NAIRA,
    bubbles_to_naira,
    kobo_to_naira,
    naira_to_kobo,
)
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import (
    create_wallet_hold,
    get_member_by_auth_id,
    internal_get,
    internal_post,
    release_wallet_hold,
)
from libs.db.session import get_async_db
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from services.payments_service.schemas import (
    CreatePaymentIntentRequest,
    PaymentIntentResponse,
)

settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2

from ._discounts import _validate_and_apply_discount
from ._entitlement import _mark_paid_and_apply
from ._helpers import (
    _require_attendance_status,
    _resolve_club_amount,
    _set_pending_tier_payment_for_payment,
)
from ._paystack import _initialize_paystack, _paystack_enabled

router = APIRouter()


def _service_error_detail(response: httpx.Response, fallback: str) -> str:
    try:
        payload = response.json()
        detail = payload.get("detail") if isinstance(payload, dict) else None
        return str(detail or fallback)
    except ValueError:
        return fallback


async def _member_for_payment_quote(member_auth_id: str) -> dict:
    try:
        member = await get_member_by_auth_id(
            member_auth_id,
            calling_service="payments",
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify your member profile. Please try again.",
        ) from exc
    if not member or not member.get("id"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member profile not found. Complete registration first.",
        )
    return member


async def _quote_ride_selection(
    *,
    member_id: str,
    session_id: uuid.UUID,
    ride_config_id: uuid.UUID | None,
    pickup_location_id: uuid.UUID | None,
    num_seats: int,
) -> dict:
    """Return one server-priced ride line, or an empty quote when unselected."""
    if ride_config_id is None and pickup_location_id is None:
        return {"total_kobo": 0, "lines": []}
    if ride_config_id is None or pickup_location_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ride_config_id and pickup_location_id must be provided together",
        )

    try:
        response = await internal_post(
            service_url=settings.TRANSPORT_SERVICE_URL,
            path="/internal/transport/ride-quotes",
            calling_service="payments",
            json={
                "member_id": member_id,
                "selections": [
                    {
                        "session_id": str(session_id),
                        "ride_config_id": str(ride_config_id),
                        "pickup_location_id": str(pickup_location_id),
                        "num_seats": num_seats,
                    }
                ],
            },
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not price the selected ride. Please try again.",
        ) from exc
    if response.status_code >= 400:
        response_status = (
            response.status_code
            if response.status_code < 500
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(
            status_code=response_status,
            detail=_service_error_detail(response, "Could not price the selected ride"),
        )

    try:
        quote = response.json()
        total_kobo = int(quote["total_kobo"])
        lines = quote["lines"]
        if len(lines) != 1:
            raise ValueError("ride quote must contain one line")
        line = lines[0]
        if (
            str(line["session_id"]) != str(session_id)
            or str(line["ride_config_id"]) != str(ride_config_id)
            or str(line["pickup_location_id"]) != str(pickup_location_id)
            or int(line["num_seats"]) != num_seats
            or int(line["amount_kobo"]) != total_kobo
        ):
            raise ValueError("ride quote does not match the requested selection")
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Transport service returned an invalid ride quote: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not validate the selected-ride price",
        ) from exc
    return {"total_kobo": total_kobo, "lines": lines}


async def _get_internal_session_quote(session_id: uuid.UUID) -> dict:
    try:
        response = await internal_get(
            service_url=settings.SESSIONS_SERVICE_URL,
            path=f"/internal/sessions/{session_id}",
            calling_service="payments",
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not price the selected session. Please try again.",
        ) from exc
    if response.status_code >= 400:
        response_status = (
            response.status_code
            if response.status_code < 500
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(
            status_code=response_status,
            detail=_service_error_detail(
                response, "Could not price the selected session"
            ),
        )
    try:
        session = response.json()
        session["pool_fee"] = int(session.get("pool_fee") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not validate the selected-session price",
        ) from exc
    return session


async def _get_internal_session_access(
    *,
    session_id: uuid.UUID,
    member_auth_id: str,
) -> dict:
    """Fetch and validate one backend-owned member/session access decision."""
    try:
        response = await internal_get(
            service_url=settings.SESSIONS_SERVICE_URL,
            path=f"/internal/sessions/{session_id}/access",
            calling_service="payments",
            params={"member_auth_id": member_auth_id},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify session access. Please try again.",
        ) from exc
    if response.status_code >= 400:
        response_status = (
            response.status_code
            if response.status_code < 500
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(
            status_code=response_status,
            detail=_service_error_detail(response, "Could not verify session access"),
        )

    try:
        access = response.json()
        uuid.UUID(str(access["member_id"]))
        for field in (
            "confirmed_booking",
            "visible",
            "bookable",
            "digest_eligible",
            "prompt_eligible",
            "sign_in_allowed",
            "sign_in_eligible",
        ):
            if not isinstance(access[field], bool):
                raise TypeError(f"{field} must be a boolean")
        if access.get("confirmed_booking_id") is not None:
            uuid.UUID(str(access["confirmed_booking_id"]))
        if access["confirmed_booking"] != bool(access.get("confirmed_booking_id")):
            raise ValueError("confirmed booking fields are inconsistent")
        if not isinstance(access["required_tier"], str):
            raise TypeError("required_tier must be a string")
    except (KeyError, TypeError, ValueError) as exc:
        logger.error("Sessions service returned an invalid access decision: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not validate session access",
        ) from exc
    return access


def _validate_bundle_reservation_quote(
    reservation: dict,
    *,
    payment_intent_id: uuid.UUID,
    session_ids: list[uuid.UUID],
) -> None:
    expected_session_ids = {str(session_id) for session_id in session_ids}
    try:
        uuid.UUID(str(reservation["member_id"]))
        if str(uuid.UUID(str(reservation["payment_intent_id"]))) != str(
            payment_intent_id
        ):
            raise ValueError("payment intent does not match")
        pool_total_kobo = int(reservation["pool_total_kobo"])
        lines = reservation["lines"]
        if pool_total_kobo < 0 or not isinstance(lines, list):
            raise ValueError("invalid reservation total or lines")

        returned_session_ids: list[str] = []
        booking_ids: list[str] = []
        line_total = 0
        for line in lines:
            session_id = str(uuid.UUID(str(line["session_id"])))
            booking_id = str(uuid.UUID(str(line["booking_id"])))
            amount_kobo = int(line["amount_kobo"])
            if amount_kobo < 0:
                raise ValueError("reservation line amount cannot be negative")
            returned_session_ids.append(session_id)
            booking_ids.append(booking_id)
            line_total += amount_kobo

        if (
            len(lines) != len(session_ids)
            or len(set(returned_session_ids)) != len(returned_session_ids)
            or set(returned_session_ids) != expected_session_ids
            or len(set(booking_ids)) != len(booking_ids)
            or line_total != pool_total_kobo
        ):
            raise ValueError("reservation lines do not match the requested bundle")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid session reservation quote: {exc}") from exc


def _validate_bundle_ride_quote(
    ride_quote: dict, *, ride_configs: dict[str, dict]
) -> None:
    try:
        total_kobo = int(ride_quote["total_kobo"])
        lines = ride_quote["lines"]
        if total_kobo < 0 or not isinstance(lines, list):
            raise ValueError("invalid ride total or lines")
        if len(lines) != len(ride_configs):
            raise ValueError("ride line count does not match selections")

        returned_session_ids: list[str] = []
        line_total = 0
        for line in lines:
            session_id = str(uuid.UUID(str(line["session_id"])))
            expected = ride_configs.get(session_id)
            if expected is None:
                raise ValueError("ride line belongs to an unselected session")
            amount_kobo = int(line["amount_kobo"])
            unit_amount_kobo = int(line["unit_amount_kobo"])
            num_seats = int(line["num_seats"])
            if amount_kobo < 0 or unit_amount_kobo < 0 or num_seats < 1:
                raise ValueError("ride line contains an invalid amount")
            if (
                str(uuid.UUID(str(line["ride_config_id"])))
                != str(expected["ride_config_id"])
                or str(uuid.UUID(str(line["pickup_location_id"])))
                != str(expected["pickup_location_id"])
                or num_seats != int(expected["num_seats"])
                or amount_kobo != unit_amount_kobo * num_seats
            ):
                raise ValueError("ride line does not match the requested selection")
            returned_session_ids.append(session_id)
            line_total += amount_kobo

        if (
            len(set(returned_session_ids)) != len(returned_session_ids)
            or set(returned_session_ids) != set(ride_configs)
            or line_total != total_kobo
        ):
            raise ValueError("ride lines do not match the requested selections")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid bundle ride quote: {exc}") from exc


async def _get_session_booking_quote(
    *,
    booking_id: uuid.UUID,
    member_auth_id: str,
) -> dict:
    try:
        response = await internal_get(
            service_url=settings.SESSIONS_SERVICE_URL,
            path=f"/internal/sessions/bookings/{booking_id}",
            calling_service="payments",
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not verify the session booking. Please try again.",
        ) from exc
    if response.status_code >= 400:
        response_status = (
            response.status_code
            if response.status_code < 500
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(
            status_code=response_status,
            detail=_service_error_detail(
                response, "Could not verify the session booking"
            ),
        )
    try:
        booking = response.json()
        booking["fee_amount_kobo"] = int(booking.get("fee_amount_kobo") or 0)
        booking_member_auth_id = str(booking["member_auth_id"])
        uuid.UUID(str(booking["member_id"]))
        uuid.UUID(str(booking["session_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Sessions service returned an invalid booking quote",
        ) from exc
    if booking_member_auth_id != member_auth_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This booking belongs to another member",
        )
    if str(booking.get("status") or "").lower() not in {"pending", "confirmed"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This booking is no longer payable",
        )
    if booking.get("wallet_transaction_id"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This booking has already been paid from the wallet",
        )
    if booking.get("payment_intent_id"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A payment is already linked to this booking",
        )
    return booking


async def _release_session_bundle_reservation(
    *, member_auth_id: str, payment_intent_id: uuid.UUID
) -> None:
    try:
        response = await internal_post(
            service_url=settings.SESSIONS_SERVICE_URL,
            path="/internal/sessions/bookings/bundle/release",
            calling_service="payments",
            json={
                "member_auth_id": member_auth_id,
                "payment_intent_id": str(payment_intent_id),
            },
        )
        if response.status_code >= 400:
            logger.error(
                "Bundle reservation release failed payment_intent_id=%s: %s %s",
                payment_intent_id,
                response.status_code,
                response.text,
            )
    except httpx.HTTPError:
        logger.exception(
            "Could not release bundle reservation payment_intent_id=%s",
            payment_intent_id,
        )


async def _reserve_and_quote_session_bundle(
    *,
    member_auth_id: str,
    payment_intent_id: uuid.UUID,
    session_ids: list[uuid.UUID],
    ride_configs: dict[str, dict],
) -> dict:
    """Reserve session capacity and obtain server-authoritative bundle pricing."""
    try:
        reservation_response = await internal_post(
            service_url=settings.SESSIONS_SERVICE_URL,
            path="/internal/sessions/bookings/bundle/reserve",
            calling_service="payments",
            json={
                "member_auth_id": member_auth_id,
                "payment_intent_id": str(payment_intent_id),
                "session_ids": [str(session_id) for session_id in session_ids],
            },
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        logger.warning("Session bundle reservation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not reserve selected sessions. Please try again.",
        ) from exc

    if reservation_response.status_code >= 400:
        response_status = (
            reservation_response.status_code
            if reservation_response.status_code < 500
            else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        raise HTTPException(
            status_code=response_status,
            detail=_service_error_detail(
                reservation_response, "Could not reserve selected sessions"
            ),
        )
    try:
        reservation = reservation_response.json()
        _validate_bundle_reservation_quote(
            reservation,
            payment_intent_id=payment_intent_id,
            session_ids=session_ids,
        )
    except (KeyError, TypeError, ValueError) as exc:
        await _release_session_bundle_reservation(
            member_auth_id=member_auth_id,
            payment_intent_id=payment_intent_id,
        )
        logger.error("Sessions service returned an invalid bundle quote: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not validate the selected-session price",
        ) from exc

    ride_quote = {"total_kobo": 0, "lines": []}
    if ride_configs:
        selections = [
            {
                "session_id": session_id,
                **selection,
            }
            for session_id, selection in ride_configs.items()
        ]
        try:
            ride_response = await internal_post(
                service_url=settings.TRANSPORT_SERVICE_URL,
                path="/internal/transport/ride-quotes",
                calling_service="payments",
                json={
                    "member_id": reservation["member_id"],
                    "selections": selections,
                },
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            await _release_session_bundle_reservation(
                member_auth_id=member_auth_id,
                payment_intent_id=payment_intent_id,
            )
            logger.warning("Session bundle ride quote failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not price selected rides. Please try again.",
            ) from exc
        if ride_response.status_code >= 400:
            await _release_session_bundle_reservation(
                member_auth_id=member_auth_id,
                payment_intent_id=payment_intent_id,
            )
            response_status = (
                ride_response.status_code
                if ride_response.status_code < 500
                else status.HTTP_503_SERVICE_UNAVAILABLE
            )
            raise HTTPException(
                status_code=response_status,
                detail=_service_error_detail(
                    ride_response, "Could not price selected rides"
                ),
            )
        try:
            ride_quote = ride_response.json()
            _validate_bundle_ride_quote(ride_quote, ride_configs=ride_configs)
        except (KeyError, TypeError, ValueError) as exc:
            await _release_session_bundle_reservation(
                member_auth_id=member_auth_id,
                payment_intent_id=payment_intent_id,
            )
            logger.error("Transport service returned an invalid bundle quote: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Could not validate the selected-ride price",
            ) from exc

    return {
        "reservation": reservation,
        "ride_quote": ride_quote,
        "total_kobo": int(reservation["pool_total_kobo"])
        + int(ride_quote["total_kobo"]),
    }


@router.post(
    "/intents",
    response_model=PaymentIntentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_payment_intent(
    payload: CreatePaymentIntentRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Create a payment intent (records a pending payment) and (if configured) initializes Paystack checkout.
    """
    payment_id = uuid.uuid4()
    bundle_reservation_active = False

    async def release_active_bundle_reservation() -> None:
        nonlocal bundle_reservation_active
        if not bundle_reservation_active:
            return
        await _release_session_bundle_reservation(
            member_auth_id=current_user.user_id,
            payment_intent_id=payment_id,
        )
        bundle_reservation_active = False

    # Community activation - ₦20,000/year
    if payload.purpose == PaymentPurpose.COMMUNITY:
        amount = float(
            getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000) * payload.years
        )
        payment_metadata = {**(payload.payment_metadata or {}), "years": payload.years}

    # Club add-on - check if community extension needed
    elif payload.purpose == PaymentPurpose.CLUB:
        amount, months, cycle = _resolve_club_amount(payload)

        # Check if Club would exceed Community membership
        community_extension_months = 0
        community_extension_amount = 0.0
        requires_community_extension = False

        # Fetch member's community_paid_until from members_service
        try:
            headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{settings.MEMBERS_SERVICE_URL}/members/by-auth/{current_user.user_id}",
                    headers=headers,
                )
                if resp.status_code == 200:
                    member_data = resp.json()
                    membership = member_data.get("membership") or {}
                    community_until_str = membership.get("community_paid_until")

                    if community_until_str:
                        from dateutil.relativedelta import relativedelta

                        community_until = datetime.fromisoformat(
                            community_until_str.replace("Z", "+00:00")
                        )
                        club_end = utc_now() + relativedelta(months=months)

                        if club_end > community_until:
                            # Calculate months needed to extend Community
                            diff_days = (club_end - community_until).days
                            community_extension_months = max(
                                1, (diff_days + 29) // 30
                            )  # Round up
                            community_monthly_rate = (
                                getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000)
                                / 12
                            )
                            community_extension_amount = round(
                                community_monthly_rate * community_extension_months, 2
                            )
                            requires_community_extension = True
        except Exception as e:
            logger.warning(f"Could not check community status: {e}")

        # If extension required and user opted in, add to total
        if requires_community_extension and payload.include_community_extension:
            amount += community_extension_amount

        payment_metadata = {
            **(payload.payment_metadata or {}),
            "months": months,
            "club_billing_cycle": str(cycle),
            "community_extension_months": (
                community_extension_months if payload.include_community_extension else 0
            ),
            "community_extension_amount": (
                community_extension_amount if payload.include_community_extension else 0
            ),
        }

    # Club bundle - Community + Club together
    elif payload.purpose == PaymentPurpose.CLUB_BUNDLE:
        community_fee = float(
            getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20000) * payload.years
        )
        club_amount, months, cycle = _resolve_club_amount(payload)
        amount = community_fee + club_amount
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "years": payload.years,
            "months": months,
            "club_billing_cycle": str(cycle),
            "components": {
                "community": community_fee,
                "club": club_amount,
            },
        }

    # Academy cohort enrollment
    elif payload.purpose == PaymentPurpose.ACADEMY_COHORT:
        if not payload.enrollment_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="enrollment_id is required for ACADEMY_COHORT payments",
            )
        # Lookup enrollment and next payable installment from academy_service.
        # Pass use_installments so the academy service can build the schedule on-demand
        # if the member opted in and no schedule exists yet.
        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.ACADEMY_SERVICE_URL}/internal/academy/enrollments/{payload.enrollment_id}",
                params={"use_installments": str(payload.use_installments).lower()},
                headers=headers,
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to fetch enrollment: {resp.text}",
                )
            enrollment_data = resp.json()
            cohort_id = enrollment_data.get("cohort_id")
            installments = sorted(
                enrollment_data.get("installments") or [],
                key=lambda i: i.get("installment_number", 0),
            )

        paid_statuses = {"paid", "waived"}
        next_installment = next(
            (
                i
                for i in installments
                if str(i.get("status") or "").lower() not in paid_statuses
            ),
            None,
        )

        if next_installment:
            # Academy returns installment amounts in kobo; convert to NGN for payment intent.
            amount = float(next_installment.get("amount") or 0) / KOBO_PER_NAIRA
        else:
            # Backward-compatible fallback for older enrollments without an installment plan.
            program = enrollment_data.get("program") or {}
            cohort = enrollment_data.get("cohort") or {}
            amount = float(
                cohort.get("price_override")
                if cohort.get("price_override") is not None
                else (program.get("price_amount") or 0)
            )
            if str(enrollment_data.get("payment_status") or "").lower() == "paid":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="All required academy installments are already paid",
                )

        # Member-initiated custom amount: must be >= next installment amount
        # and <= remaining balance (founder policy May 2026). Default behavior
        # without an override is unchanged — charge exactly the stipulated amount.
        if (
            payload.amount_override_kobo is not None
            and payload.amount_override_kobo > 0
        ):
            override_naira = payload.amount_override_kobo / KOBO_PER_NAIRA
            if override_naira < amount:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Custom amount NGN {override_naira:,.2f} is less than the "
                        f"next stipulated installment NGN {amount:,.2f}"
                    ),
                )
            remaining_balance_kobo = sum(
                int(i.get("amount") or 0)
                for i in installments
                if str(i.get("status") or "").lower() not in paid_statuses
            )
            remaining_balance_naira = remaining_balance_kobo / KOBO_PER_NAIRA
            if override_naira > remaining_balance_naira:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Custom amount NGN {override_naira:,.2f} exceeds remaining "
                        f"balance NGN {remaining_balance_naira:,.2f}"
                    ),
                )
            amount = override_naira

        if amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No payable installment is available for this enrollment",
            )

        payment_metadata = {
            **(payload.payment_metadata or {}),
            "enrollment_id": str(payload.enrollment_id),
            "cohort_id": str(cohort_id) if cohort_id else None,
            "installment_id": (
                str(next_installment.get("id")) if next_installment else None
            ),
            "installment_number": (
                int(next_installment.get("installment_number"))
                if next_installment and next_installment.get("installment_number")
                else None
            ),
            "installment_due_at": (
                next_installment.get("due_at") if next_installment else None
            ),
            "total_installments": (
                int(enrollment_data.get("total_installments") or 0) or None
            ),
        }

    # Store order payment
    elif payload.purpose == PaymentPurpose.STORE_ORDER:
        if not payload.order_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="order_id is required for STORE_ORDER payments",
            )
        # Lookup order and total from store_service
        headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{settings.STORE_SERVICE_URL}/store/admin/orders/{payload.order_id}",
                headers=headers,
            )
            if resp.status_code >= 400:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to fetch order: {resp.text}",
                )
            order_data = resp.json()
            amount = float(order_data.get("total_ngn") or 0)

        if amount <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Order total must be greater than zero",
            )

        payment_metadata = {
            **(payload.payment_metadata or {}),
            "order_id": str(payload.order_id),
            "order_number": order_data.get("order_number"),
        }
        if payload.bubbles_to_apply:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Store Bubbles must be selected through store checkout",
            )

    # Session fee payment (pool fee + ride share)
    elif payload.purpose == PaymentPurpose.SESSION_FEE:
        if not payload.session_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session_id is required for SESSION_FEE payments",
            )
        member = await _member_for_payment_quote(current_user.user_id)
        access = await _get_internal_session_access(
            session_id=payload.session_id,
            member_auth_id=current_user.user_id,
        )
        if not access["sign_in_allowed"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    access.get("message")
                    or "Session sign-in is not currently available."
                ),
            )
        session_quote = await _get_internal_session_quote(payload.session_id)
        ride_quote = await _quote_ride_selection(
            member_id=str(member["id"]),
            session_id=payload.session_id,
            ride_config_id=payload.ride_config_id,
            pickup_location_id=payload.pickup_location_id,
            num_seats=payload.num_seats,
        )
        authoritative_total_kobo = int(session_quote["pool_fee"]) + int(
            ride_quote["total_kobo"]
        )
        if (
            payload.direct_amount is not None
            and naira_to_kobo(payload.direct_amount) != authoritative_total_kobo
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The session price changed. Refresh before paying.",
            )
        amount = kobo_to_naira(authoritative_total_kobo)
        attendance_status = _require_attendance_status(
            payload.attendance_status,
            source="attendance_status",
        )
        ride_line = ride_quote["lines"][0] if ride_quote["lines"] else None
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "session_id": str(payload.session_id),
            "ride_config_id": (str(ride_line["ride_config_id"]) if ride_line else None),
            "pickup_location_id": (
                str(ride_line["pickup_location_id"]) if ride_line else None
            ),
            "attendance_status": attendance_status.value,
            "num_seats": int(ride_line["num_seats"]) if ride_line else 1,
            "passengers": (
                [passenger.model_dump() for passenger in payload.passengers]
                if ride_line and payload.passengers is not None
                else None
            ),
            "bubbles_to_apply": payload.bubbles_to_apply or 0,
            "server_price": {
                "pool_total_kobo": int(session_quote["pool_fee"]),
                "ride_total_kobo": int(ride_quote["total_kobo"]),
                "total_kobo": authoritative_total_kobo,
            },
        }

    # Session booking — A1 Phase 3.3 Paystack pre-booking. The PENDING
    # SessionBooking was already created by sessions_service; this intent
    # just carries booking_id so the entitlement handler can confirm it.
    elif payload.purpose == PaymentPurpose.SESSION_BOOKING:
        raw_booking_id = (payload.payment_metadata or {}).get("booking_id")
        if not raw_booking_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "payment_metadata.booking_id is required for "
                    "SESSION_BOOKING payments"
                ),
            )
        try:
            booking_id = uuid.UUID(str(raw_booking_id))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="payment_metadata.booking_id must be a valid UUID",
            ) from exc
        booking_quote = await _get_session_booking_quote(
            booking_id=booking_id,
            member_auth_id=current_user.user_id,
        )
        booking_session_id = uuid.UUID(str(booking_quote["session_id"]))
        if payload.session_id is not None and payload.session_id != booking_session_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The booking does not belong to the selected session",
            )
        ride_quote = await _quote_ride_selection(
            member_id=str(booking_quote["member_id"]),
            session_id=booking_session_id,
            ride_config_id=payload.ride_config_id,
            pickup_location_id=payload.pickup_location_id,
            num_seats=payload.num_seats,
        )
        authoritative_total_kobo = int(booking_quote["fee_amount_kobo"]) + int(
            ride_quote["total_kobo"]
        )
        if (
            payload.direct_amount is not None
            and naira_to_kobo(payload.direct_amount) != authoritative_total_kobo
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The booking price changed. Refresh before paying.",
            )
        amount = kobo_to_naira(authoritative_total_kobo)
        ride_line = ride_quote["lines"][0] if ride_quote["lines"] else None
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "booking_id": str(booking_id),
            "session_id": str(booking_session_id),
            "member_id": str(booking_quote["member_id"]),
            "ride_config_id": (str(ride_line["ride_config_id"]) if ride_line else None),
            "pickup_location_id": (
                str(ride_line["pickup_location_id"]) if ride_line else None
            ),
            "num_seats": int(ride_line["num_seats"]) if ride_line else 1,
            "passengers": (
                [passenger.model_dump() for passenger in payload.passengers]
                if ride_line and payload.passengers is not None
                else None
            ),
            "bubbles_to_apply": payload.bubbles_to_apply or 0,
            "server_price": {
                "pool_total_kobo": int(booking_quote["fee_amount_kobo"]),
                "ride_total_kobo": int(ride_quote["total_kobo"]),
                "total_kobo": authoritative_total_kobo,
            },
        }

    # Session bundle — book multiple sessions in one payment intent
    elif payload.purpose == PaymentPurpose.SESSION_BUNDLE:
        if payload.payment_method != "paystack":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Session bundles must be settled during online checkout",
            )
        if not payload.session_ids or len(payload.session_ids) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session_ids is required for SESSION_BUNDLE payments",
            )
        if len(payload.session_ids) > 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Maximum 10 sessions per bundle",
            )
        # Check for duplicates
        unique_ids = list({str(sid) for sid in payload.session_ids})
        if len(unique_ids) != len(payload.session_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate session_ids in bundle",
            )
        if payload.direct_amount is not None and payload.direct_amount < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="direct_amount cannot be negative for SESSION_BUNDLE payments",
            )
        # Validate per-session ride configs (if provided) — every key must be
        # one of the session_ids in the bundle.
        ride_configs_meta: dict = {}
        if payload.session_ride_configs:
            bundle_id_set = {str(sid) for sid in payload.session_ids}
            for sid_key, ride_cfg in payload.session_ride_configs.items():
                if str(sid_key) not in bundle_id_set:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"session_ride_configs key {sid_key} is not in session_ids",
                    )
                ride_configs_meta[str(sid_key)] = {
                    "ride_config_id": str(ride_cfg.ride_config_id),
                    "pickup_location_id": str(ride_cfg.pickup_location_id),
                    "num_seats": int(ride_cfg.num_seats),
                    "passengers": (
                        [passenger.model_dump() for passenger in ride_cfg.passengers]
                        if ride_cfg.passengers is not None
                        else None
                    ),
                }
        bundle_quote = await _reserve_and_quote_session_bundle(
            member_auth_id=current_user.user_id,
            payment_intent_id=payment_id,
            session_ids=payload.session_ids,
            ride_configs=ride_configs_meta,
        )
        bundle_reservation_active = True
        authoritative_total_kobo = int(bundle_quote["total_kobo"])
        if (
            payload.direct_amount is not None
            and naira_to_kobo(payload.direct_amount) != authoritative_total_kobo
        ):
            await release_active_bundle_reservation()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The bundle price changed. Refresh the selected sessions "
                    "before paying."
                ),
            )
        amount = kobo_to_naira(authoritative_total_kobo)
        reservation = bundle_quote["reservation"]
        ride_quote = bundle_quote["ride_quote"]
        quoted_ride_configs = {
            str(line["session_id"]): {
                "ride_config_id": str(line["ride_config_id"]),
                "pickup_location_id": str(line["pickup_location_id"]),
                "num_seats": int(line["num_seats"]),
                "passengers": ride_configs_meta.get(str(line["session_id"]), {}).get(
                    "passengers"
                ),
            }
            for line in ride_quote["lines"]
        }
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "session_ids": [str(sid) for sid in payload.session_ids],
            "session_count": len(payload.session_ids),
            "booking_ids": [str(line["booking_id"]) for line in reservation["lines"]],
            "member_id": str(reservation["member_id"]),
            "session_ride_configs": quoted_ride_configs or None,
            "bundle_price": {
                "pool_total_kobo": int(reservation["pool_total_kobo"]),
                "ride_total_kobo": int(ride_quote["total_kobo"]),
                "total_kobo": authoritative_total_kobo,
                "session_lines": reservation["lines"],
                "ride_lines": ride_quote["lines"],
            },
        }

    # Standalone ride share payment (after session already booked)
    elif payload.purpose == PaymentPurpose.RIDE_SHARE:
        if not payload.session_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="session_id is required for RIDE_SHARE payments",
            )
        if not payload.ride_config_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ride_config_id is required for RIDE_SHARE payments",
            )
        if not payload.pickup_location_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="pickup_location_id is required for RIDE_SHARE payments",
            )
        access = await _get_internal_session_access(
            session_id=payload.session_id,
            member_auth_id=current_user.user_id,
        )
        if not access["confirmed_booking"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Book this session before purchasing its ride share.",
            )
        member = await _member_for_payment_quote(current_user.user_id)
        ride_quote = await _quote_ride_selection(
            member_id=str(member["id"]),
            session_id=payload.session_id,
            ride_config_id=payload.ride_config_id,
            pickup_location_id=payload.pickup_location_id,
            num_seats=payload.num_seats,
        )
        authoritative_total_kobo = int(ride_quote["total_kobo"])
        if (
            payload.direct_amount is not None
            and naira_to_kobo(payload.direct_amount) != authoritative_total_kobo
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The ride price changed. Refresh before paying.",
            )
        amount = kobo_to_naira(authoritative_total_kobo)
        ride_line = ride_quote["lines"][0]
        payment_metadata = {
            **(payload.payment_metadata or {}),
            "session_id": str(payload.session_id),
            "member_id": str(member["id"]),
            "ride_config_id": str(ride_line["ride_config_id"]),
            "pickup_location_id": str(ride_line["pickup_location_id"]),
            "num_seats": int(ride_line["num_seats"]),
            "passengers": (
                [passenger.model_dump() for passenger in payload.passengers]
                if payload.passengers is not None
                else None
            ),
            "server_price": {
                "ride_total_kobo": authoritative_total_kobo,
                "total_kobo": authoritative_total_kobo,
            },
        }

    else:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Payment intent not implemented for purpose={payload.purpose}",
        )

    # Apply discount if provided
    original_amount = amount
    discount_applied = None
    discount_code_used = None

    if payload.discount_code:
        # Get components for smart discount matching (CLUB_BUNDLE has components in metadata)
        discount_components = (
            payment_metadata.get("components")
            if payload.purpose == PaymentPurpose.CLUB_BUNDLE
            else None
        )

        try:
            (
                amount,
                discount_applied,
                discount_obj,
                applies_to_component,
            ) = await _validate_and_apply_discount(
                db=db,
                discount_code=payload.discount_code,
                purpose=payload.purpose,
                original_amount=original_amount,
                member_auth_id=current_user.user_id,
                components=discount_components,
            )
        except Exception:
            await release_active_bundle_reservation()
            raise
        if discount_obj:
            discount_code_used = discount_obj.code
            payment_metadata = {
                **payment_metadata,
                "discount_code": discount_obj.code,
                "discount_type": discount_obj.discount_type.value,
                "discount_value": discount_obj.value,
                "discount_applied": discount_applied,
                "original_amount": original_amount,
                "discount_applies_to_component": applies_to_component,
            }

    bubbles_purposes = {
        PaymentPurpose.SESSION_FEE,
        PaymentPurpose.SESSION_BOOKING,
        PaymentPurpose.SESSION_BUNDLE,
        PaymentPurpose.RIDE_SHARE,
    }
    bubbles_to_apply_val = payload.bubbles_to_apply or 0
    wallet_hold_id: str | None = None

    async def release_active_wallet_hold() -> None:
        nonlocal wallet_hold_id
        if wallet_hold_id is None:
            return
        try:
            await release_wallet_hold(wallet_hold_id, calling_service="payments")
        except httpx.HTTPError:
            logger.exception("Failed to release wallet hold %s", wallet_hold_id)
        wallet_hold_id = None

    if bubbles_to_apply_val > 0:
        if payload.purpose not in bubbles_purposes:
            await release_active_bundle_reservation()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bubbles cannot be applied to this payment type",
            )
        if payload.payment_method != "paystack":
            await release_active_bundle_reservation()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Bubbles can only be combined with online payment",
            )

        amount_kobo = naira_to_kobo(amount)
        maximum_bubbles = amount_kobo // KOBO_PER_BUBBLE
        if bubbles_to_apply_val > maximum_bubbles:
            await release_active_bundle_reservation()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"At most {maximum_bubbles} whole Bubbles can be applied "
                    "to this payment"
                ),
            )
        try:
            hold = await create_wallet_hold(
                current_user.user_id,
                amount=bubbles_to_apply_val,
                idempotency_key=f"payment-intent:{payment_id}:bubbles",
                description=f"Payment {payload.purpose.value}",
                calling_service="payments",
                reference_type=payload.purpose.value,
                reference_id=str(payment_id),
                expires_in_seconds=1800,
            )
        except httpx.HTTPStatusError as exc:
            await release_active_bundle_reservation()
            upstream_status = exc.response.status_code
            detail = _service_error_detail(
                exc.response, "Could not reserve the selected Bubbles"
            )
            raise HTTPException(
                status_code=(
                    upstream_status
                    if upstream_status in {400, 402, 404, 409}
                    else status.HTTP_503_SERVICE_UNAVAILABLE
                ),
                detail=detail,
            ) from exc
        wallet_hold_id = str(hold["id"])
        amount = kobo_to_naira(amount_kobo - (bubbles_to_apply_val * KOBO_PER_BUBBLE))
        payment_metadata = {
            **payment_metadata,
            "bubbles_to_apply": bubbles_to_apply_val,
            "bubbles_value_ngn": bubbles_to_naira(bubbles_to_apply_val),
            "wallet_hold_id": wallet_hold_id,
        }

    payment = Payment(
        id=payment_id,
        reference=Payment.generate_reference(),
        member_auth_id=current_user.user_id,
        payer_email=current_user.email,
        purpose=payload.purpose,
        amount=amount,
        currency=payload.currency,
        status=PaymentStatus.PENDING,
        payment_method=payload.payment_method,  # paystack or manual_transfer
        payment_metadata=payment_metadata,
    )

    db.add(payment)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        await release_active_wallet_hold()
        await release_active_bundle_reservation()
        raise
    await db.refresh(payment)

    checkout_url = None

    # Paystack (and most payment providers) cannot initialize a transaction for 0 NGN.
    # A full discount, full Bubbles settlement, or genuinely free server-priced
    # bundle is completed internally and its entitlement applied immediately.
    if payment.amount <= 0:
        internally_settleable = bool(payload.discount_code) or (
            payload.purpose in bubbles_purposes
            and (bubbles_to_apply_val > 0 or original_amount <= 0)
        )
        if not internally_settleable:
            await release_active_wallet_hold()
            await release_active_bundle_reservation()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Amount must be greater than zero",
            )
        if payload.discount_code:
            settlement_provider = "discount"
            settlement_reference = f"discount:{(discount_code_used or payload.discount_code).upper().strip()}"
        else:
            settlement_provider = "internal"
            settlement_reference = f"free:{payment.reference}"
        # Once settlement starts, entitlement retry owns the reservations.
        bundle_reservation_active = False
        payment = await _mark_paid_and_apply(
            db=db,
            payment=payment,
            provider=settlement_provider,
            provider_reference=settlement_reference,
            paid_at=utc_now(),
            provider_payload={
                "discount_code": discount_code_used or payload.discount_code,
                "discount_applied": discount_applied,
                "original_amount": original_amount,
                "bubbles_to_apply": bubbles_to_apply_val,
            },
        )
        wallet_hold_id = None

    # Only initialize Paystack for online payments
    if (
        payment.status == PaymentStatus.PENDING
        and payload.payment_method == "paystack"
        and _paystack_enabled()
    ):
        if not current_user.email:
            payment.status = PaymentStatus.FAILED
            db.add(payment)
            await db.commit()
            await release_active_wallet_hold()
            await release_active_bundle_reservation()
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Authenticated user email is required to initialize Paystack",
            )
        # Determine redirect path based on purpose
        redirect_path = None
        if payload.purpose == PaymentPurpose.ACADEMY_COHORT and payload.enrollment_id:
            redirect_path = f"/account/academy/enrollment-success?enrollment_id={payload.enrollment_id}"

        try:
            authorization_url, access_code = await _initialize_paystack(
                payment, current_user.email, redirect_path
            )
        except Exception:
            payment.status = PaymentStatus.FAILED
            payment.payment_metadata = {
                **(payment.payment_metadata or {}),
                "checkout_initialization_failed_at": utc_now().isoformat(),
            }
            db.add(payment)
            await db.commit()
            await release_active_wallet_hold()
            await release_active_bundle_reservation()
            raise
        checkout_url = authorization_url
        payment.provider = "paystack"
        payment.provider_reference = payment.reference
        payment.payment_metadata = {
            **(payment.payment_metadata or {}),
            "paystack": {
                "authorization_url": authorization_url,
                "access_code": access_code,
            },
        }
        db.add(payment)
        await db.commit()
        await db.refresh(payment)

    if (
        payment.status == PaymentStatus.PENDING
        and payload.payment_method == "paystack"
        and not _paystack_enabled()
    ):
        payment.status = PaymentStatus.FAILED
        db.add(payment)
        await db.commit()
        await release_active_wallet_hold()
        await release_active_bundle_reservation()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Online payment is temporarily unavailable. Please try again later.",
        )

    # Save pending payment reference to member for cross-device resumption
    if payment.status == PaymentStatus.PENDING:
        await _set_pending_tier_payment_for_payment(payment)

    # Build extension info for response (only for CLUB payments)
    response_extension_info = {}
    if payload.purpose == PaymentPurpose.CLUB:
        response_extension_info = {
            "requires_community_extension": requires_community_extension,
            "community_extension_months": community_extension_months,
            "community_extension_amount": community_extension_amount,
            "total_with_extension": (
                payment.amount + community_extension_amount
                if not payload.include_community_extension
                else None
            ),
        }

    return PaymentIntentResponse(
        reference=payment.reference,
        amount=payment.amount,
        currency=payment.currency,
        purpose=payment.purpose,
        status=payment.status,
        checkout_url=checkout_url,
        created_at=payment.created_at,
        original_amount=original_amount if discount_applied else None,
        discount_applied=discount_applied,
        discount_code=discount_code_used,
        **response_extension_info,
    )
