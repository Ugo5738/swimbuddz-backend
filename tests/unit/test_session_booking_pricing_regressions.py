import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from libs.common.session_access import evaluate_session_access
from services.sessions_service.routers import bookings
from services.sessions_service.schemas.booking import SessionBookingCreate
from services.sessions_service.services.pricing import normalize_pricing_payload
from tests.conftest import make_member_user


def cohort_session(fee):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        id=uuid.uuid4(),
        title="Extra cohort practice",
        session_type="cohort_class",
        cohort_id=uuid.uuid4(),
        status="scheduled",
        starts_at=now + timedelta(days=1),
        ends_at=now + timedelta(days=1, hours=1),
        pool_fee=fee,
        cohort_fee_mode="paid_extra",
        guest_fee_kobo=None,
        allows_guests=False,
        max_guests_per_booking=0,
    )


@pytest.mark.parametrize("fee", [0, 805000, 1500000])
@pytest.mark.parametrize("wallet", [False, True])
@pytest.mark.parametrize("mode", ["included", "paid_extra", None])
async def test_cohort_booking_uses_explicit_inclusion_not_pool_cost(
    monkeypatch, fee, wallet, mode
):
    session = cohort_session(fee)
    session.cohort_fee_mode = mode
    fee = fee if mode == "paid_extra" else 0
    member_id = uuid.uuid4()
    decision = evaluate_session_access(
        {}, session, cohort_enrollment={"enrolled": True}
    )
    assert decision.fee_amount_kobo == fee
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: session),
                SimpleNamespace(scalar_one_or_none=lambda: None),
            ]
        ),
        add=Mock(),
        flush=AsyncMock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    monkeypatch.setattr(
        bookings,
        "_resolve_member_for_user",
        AsyncMock(return_value=(member_id, "auth")),
    )
    monkeypatch.setattr(
        bookings, "evaluate_member_session_access", AsyncMock(return_value=decision)
    )
    monkeypatch.setattr(bookings, "assert_booking_capacity", AsyncMock())
    monkeypatch.setattr(bookings, "_replace_guests", AsyncMock())
    sync = AsyncMock()
    monkeypatch.setattr(bookings, "sync_booking_attendance", sync)
    monkeypatch.setattr(bookings, "_send_direct_booking_confirmation", AsyncMock())
    debit = AsyncMock(return_value=uuid.uuid4() if fee and wallet else None)
    monkeypatch.setattr(bookings, "_debit_booking_fee", debit)
    result = await bookings.book_session(
        session.id,
        SessionBookingCreate(
            session_id=session.id, fee_amount_kobo=1, pay_with_bubbles=wallet
        ),
        make_member_user(),
        db,
    )
    assert result.fee_amount_kobo == fee  # ignores the forged client amount
    assert result.member_fee_amount_kobo == fee
    assert result.access_source == "cohort_enrollment"
    assert result.status.value == ("confirmed" if wallet or fee == 0 else "pending")
    if fee and not wallet:
        debit.assert_not_awaited()
        sync.assert_not_awaited()
        assert result.expires_at is not None
        assert result.payment_intent_id is None
    else:
        debit.assert_awaited_once()
        assert debit.await_args.kwargs["fee_amount_kobo"] == fee


def test_cohort_enrollment_still_required_and_suspension_respected():
    session = cohort_session(100000)
    assert not evaluate_session_access({}, session).bookable
    assert not evaluate_session_access(
        {}, session, cohort_enrollment={"enrolled": True, "access_suspended": True}
    ).bookable


def test_legacy_regular_class_with_nonzero_pool_cost_is_included_in_tuition():
    session = cohort_session(1500000)
    del session.cohort_fee_mode
    decision = evaluate_session_access(
        {}, session, cohort_enrollment={"enrolled": True}
    )
    assert decision.bookable
    assert decision.fee_amount_kobo == 0
    assert decision.price_label == "Included in Academy tuition"


def test_new_cohort_sessions_default_to_included_and_extra_mode_is_cohort_only():
    from pydantic import ValidationError
    from services.sessions_service.schemas.main import SessionCreate

    session = cohort_session(1500000)
    fields = dict(
        title="Regular class",
        session_type="cohort_class",
        cohort_id=session.cohort_id,
        starts_at=session.starts_at,
        ends_at=session.ends_at,
        pool_fee=15000,
    )
    assert SessionCreate(**fields).cohort_fee_mode == "included"
    assert (
        SessionCreate(**fields, cohort_fee_mode="paid_extra").cohort_fee_mode
        == "paid_extra"
    )
    with pytest.raises(ValidationError, match="Only a cohort class"):
        SessionCreate(
            **{**fields, "session_type": "community", "cohort_id": None},
            cohort_fee_mode="paid_extra",
        )


@pytest.mark.parametrize("mode,expected", [("included", 0), ("paid_extra", 1500000)])
async def test_admin_walk_in_does_not_charge_regular_class_pool_cost(
    monkeypatch, mode, expected
):
    from libs.common import service_client
    from services.sessions_service.schemas.booking import AdminWalkInRequest
    from tests.conftest import make_admin_user

    session = cohort_session(1500000)
    session.cohort_fee_mode = mode
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: session),
                SimpleNamespace(scalar_one_or_none=lambda: None),
            ]
        ),
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    monkeypatch.setattr(
        service_client,
        "get_member_by_id",
        AsyncMock(return_value={"auth_id": "member-auth"}),
    )
    monkeypatch.setattr(bookings, "_record_walk_in_attendance", AsyncMock())
    result = await bookings.admin_walk_in_booking(
        session.id, AdminWalkInRequest(member_id=uuid.uuid4()), make_admin_user(), db
    )
    assert result.fee_amount_kobo == expected
    assert result.payment_intent_id is None
    assert result.wallet_transaction_id is None
    assert result.access_source == "admin_walk_in"


async def test_walk_in_cannot_charge_an_included_class_even_with_explicit_client_fee(
    monkeypatch,
):
    from libs.common import service_client
    from services.sessions_service.schemas.booking import AdminWalkInRequest
    from tests.conftest import make_admin_user

    session = cohort_session(1500000)
    session.cohort_fee_mode = "included"
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: session)
        ),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(
        service_client,
        "get_member_by_id",
        AsyncMock(return_value={"auth_id": "member-auth"}),
    )
    with pytest.raises(HTTPException) as exc:
        await bookings.admin_walk_in_booking(
            session.id,
            AdminWalkInRequest(member_id=uuid.uuid4(), fee_amount_kobo=1500000),
            make_admin_user(),
            db,
        )
    assert exc.value.status_code == 422
    db.commit.assert_not_awaited()


def price(expected=2, capacity=2, staff=1):
    return normalize_pricing_payload(
        {
            "pricing_mode": "cost_plus",
            "capacity": capacity,
            "pricing_expected_attendees": expected,
            "margin_value": 1000,
            "cost_lines": [
                {
                    "charge_basis": "per_attendee",
                    "unit_cost_naira": 6300,
                    "quantity": expected,
                },
                {
                    "charge_basis": "per_staff",
                    "unit_cost_naira": 3500,
                    "quantity": staff,
                },
            ],
        }
    )


def test_per_swimmer_cost_plus_shared_staff_cost_and_margin():
    assert price()["pool_fee"] == 9050  # 6300 + 3500/2 + 1000
    assert price(expected=20, capacity=20)["pool_fee"] == 7475
    assert price(staff=2)["pool_fee"] == 10800


def test_cost_plus_rejects_stale_twenty_attendees_for_two_places():
    with pytest.raises(HTTPException) as exc:
        price(expected=20)
    assert exc.value.status_code == 422


def test_manual_price_is_not_divided_by_capacity():
    assert "pool_fee" not in normalize_pricing_payload(
        {"pricing_mode": "manual", "capacity": 2, "pool_fee": 9800}
    )


async def test_paid_booking_snapshot_is_unchanged_after_session_price_edit(monkeypatch):
    session = cohort_session(2000000)
    from services.sessions_service.models import SessionBookingStatus

    original = SimpleNamespace(
        status=SessionBookingStatus.CONFIRMED,
        fee_amount_kobo=1500000,
        payment_intent_id=uuid.uuid4(),
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: session),
                SimpleNamespace(scalar_one_or_none=lambda: original),
            ]
        )
    )
    monkeypatch.setattr(
        bookings,
        "_resolve_member_for_user",
        AsyncMock(return_value=(uuid.uuid4(), "auth")),
    )
    monkeypatch.setattr(bookings, "sync_booking_attendance", AsyncMock())
    price_resolver = AsyncMock()
    monkeypatch.setattr(bookings, "evaluate_member_session_access", price_resolver)
    result = await bookings.book_session(
        session.id, SessionBookingCreate(session_id=session.id), make_member_user(), db
    )
    assert result is original
    assert result.fee_amount_kobo == 1500000
    price_resolver.assert_not_awaited()
