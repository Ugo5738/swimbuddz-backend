import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.sessions_service.models import SessionBookingStatus
from services.sessions_service.routers.bookings import reconcile_missing_cohort_fee
from services.sessions_service.schemas.booking import AdminUnpricedCohortBookingRequest
from tests.conftest import make_admin_user


def records(**overrides):
    values = dict(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        status=SessionBookingStatus.CONFIRMED,
        fee_amount_kobo=0,
        member_fee_amount_kobo=0,
        access_source=None,
        payment_intent_id=None,
        wallet_transaction_id=None,
        corporate_program_id=None,
        party_size=1,
        notes="Original booking note",
    )
    values.update(overrides)
    booking = SimpleNamespace(**values)
    session = SimpleNamespace(
        id=booking.session_id,
        session_type="cohort_class",
        cohort_fee_mode="paid_extra",
        cohort_id=uuid.uuid4(),
        pool_fee=1500000,
        starts_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                SimpleNamespace(scalar_one_or_none=lambda: booking),
                SimpleNamespace(scalar_one_or_none=lambda: session),
            ]
        ),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )
    return booking, session, db


def request():
    return AdminUnpricedCohortBookingRequest(
        fee_amount_kobo=1500000,
        reason="Verified originally agreed fee against bank receipt; legacy booking missed the price.",
    )


async def test_explicit_correction_is_audited_and_does_not_mark_payment_or_change_attendance():
    booking, session, db = records()
    session.pool_fee = 1800000  # a later edit does not determine this correction
    result = await reconcile_missing_cohort_fee(
        booking.id, request(), make_admin_user(), db
    )
    assert result.fee_amount_kobo == result.member_fee_amount_kobo == 1500000
    assert result.payment_intent_id is None
    assert result.wallet_transaction_id is None
    assert result.status == SessionBookingStatus.CONFIRMED
    assert result.access_source == "cohort_fee_reconciliation"
    assert '"previous_fee_kobo": 0' in result.notes
    assert '"corrected_fee_kobo": 1500000' in result.notes
    assert "Original booking note" in result.notes
    assert request().reason in result.notes
    db.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "overrides",
    [
        {"payment_intent_id": uuid.uuid4()},
        {"wallet_transaction_id": uuid.uuid4()},
        {"fee_amount_kobo": 1500000},
        {"member_fee_amount_kobo": 1500000},
        {"access_source": "cohort_enrollment"},
        {"party_size": 2},
        {"corporate_program_id": uuid.uuid4()},
        {"status": SessionBookingStatus.PENDING},
    ],
)
async def test_paid_priced_deliberately_free_and_other_bookings_cannot_be_rewritten(
    overrides,
):
    booking, _, db = records(**overrides)
    with pytest.raises(HTTPException) as exc:
        await reconcile_missing_cohort_fee(booking.id, request(), make_admin_user(), db)
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "changes",
    [
        {"session_type": "club"},
        {"pool_fee": 0},
        {"cohort_fee_mode": "included"},
        {"cohort_id": None},
        {"starts_at": datetime.now(timezone.utc) + timedelta(days=1)},
    ],
)
async def test_only_past_paid_cohort_classes_are_eligible(changes):
    booking, session, db = records()
    for key, value in changes.items():
        setattr(session, key, value)
    with pytest.raises(HTTPException) as exc:
        await reconcile_missing_cohort_fee(booking.id, request(), make_admin_user(), db)
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()


async def test_orphan_booking_is_not_silently_attached_to_a_different_session():
    booking, _, db = records()
    db.execute.side_effect = [
        SimpleNamespace(scalar_one_or_none=lambda: booking),
        SimpleNamespace(scalar_one_or_none=lambda: None),
    ]
    with pytest.raises(HTTPException) as exc:
        await reconcile_missing_cohort_fee(booking.id, request(), make_admin_user(), db)
    assert exc.value.status_code == 409
    assert "original session is missing" in exc.value.detail
    db.commit.assert_not_awaited()
