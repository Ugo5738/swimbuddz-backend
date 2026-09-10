from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from services.members_service.routers import (
    clubs,
    community_experiences,
    experience_admin,
)
from services.members_service.schemas.club import (
    ActivateClubApplicationRequest,
    ClubApplicationReservationRequest,
)
from services.members_service.services import experience_ticketing
from services.payments_service.routers import charges
from services.payments_service.routers.intents import intent_creation


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [None, False, True])
@pytest.mark.parametrize(
    "module,helper",
    [
        (charges, "_club_context"),
        (intent_creation, "_approved_club_application_context"),
    ],
)
async def test_both_quote_and_intent_forward_explicit_choice(
    monkeypatch, selected, module, helper
):
    get = AsyncMock(return_value=httpx.Response(200, json={"subtotal_kobo": 0}))
    client = SimpleNamespace(get=get)
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **kwargs: context)
    monkeypatch.setattr(module, "_service_role_jwt", lambda service: "test-token")
    await getattr(module, helper)(uuid4(), "transition_per_session", selected)
    params = get.await_args.kwargs["params"]
    if selected is None:
        assert "community_experience_selected" not in params
    else:
        assert params["community_experience_selected"] == str(selected).lower()


@pytest.fixture
def transition(monkeypatch):
    now = datetime(2026, 9, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(clubs, "utc_now", lambda: now)
    application = SimpleNamespace(
        id=uuid4(),
        member_id=uuid4(),
        club_id=uuid4(),
        status="approved",
        selected_payment_mode=None,
        approved_payment_modes=["transition_per_session"],
        transition_expires_at=date(2026, 12, 31),
        preferred_pod_id=None,
        community_experience_selected=False,
    )
    offering = SimpleNamespace(
        id=uuid4(),
        currency="NGN",
        standard_member_fee_kobo=5_000_000,
        club_bundle_fee_kobo=3_000_000,
        event_links=[object()],
    )
    plan = SimpleNamespace(
        id=uuid4(), community_experience_offering_id=offering.id, currency="NGN"
    )
    member = SimpleNamespace(id=application.member_id)
    monkeypatch.setattr(
        clubs, "_selected_application_plans", AsyncMock(return_value=[plan])
    )
    monkeypatch.setattr(clubs, "_assert_plan_capacity", AsyncMock())
    monkeypatch.setattr(clubs, "_assert_pod_capacity", AsyncMock(return_value=None))
    monkeypatch.setattr(clubs, "_application_out", AsyncMock())
    monkeypatch.setattr(
        experience_admin, "lock_offering", AsyncMock(return_value=offering)
    )
    db = SimpleNamespace(
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
        flush=AsyncMock(),
        get=AsyncMock(return_value=member),
    )
    return application, offering, plan, member, db


def result(value=None, rows=()):
    return SimpleNamespace(scalar_one_or_none=lambda: value, scalars=lambda: rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [3_000_000, 4_000_000, 5_000_000])
async def test_transition_reservation_rechecks_standard_price_and_reserves_ticket(
    monkeypatch, transition, amount
):
    application, offering, plan, member, db = transition
    db.execute = AsyncMock(side_effect=[result(application), result(rows=[])])
    reserve = AsyncMock()
    monkeypatch.setattr(experience_ticketing, "reserve_bundle_ticket", reserve)
    body = ClubApplicationReservationRequest(
        payment_reference="PAY-AY",
        payment_mode="transition_per_session",
        community_experience_selected=True,
        community_experience_fee_kobo=amount,
    )
    if amount != 5_000_000:
        with pytest.raises(HTTPException, match="price changed"):
            await clubs.reserve_club_application_capacity(
                application.id, body, None, db
            )
        reserve.assert_not_called()
    else:
        await clubs.reserve_club_application_capacity(application.id, body, None, db)
        assert reserve.await_args.kwargs["ticket_kind"] == "standard_member"
        assert reserve.await_args.kwargs["amount_kobo"] == 5_000_000


@pytest.mark.asyncio
async def test_transition_activation_fulfils_experience_at_paid_snapshot(
    monkeypatch, transition
):
    application, offering, plan, member, db = transition
    # Admin changed the advertised price after the payment was booked.
    offering.standard_member_fee_kobo = 7_000_000
    enrollment = SimpleNamespace(id=uuid4())
    db.execute = AsyncMock(
        side_effect=[
            result(application),
            result(rows=[]),
            result(),
            result(),
            result(),
            result(),
        ]
    )
    monkeypatch.setattr(clubs, "_new_club_enrollment", Mock(return_value=enrollment))
    confirm = AsyncMock()
    monkeypatch.setattr(experience_ticketing, "confirm_bundle_ticket", confirm)
    await clubs.activate_club_application(
        application.id,
        ActivateClubApplicationRequest(
            payment_reference="PAY-AY",
            payment_mode="transition_per_session",
            community_experience_selected=True,
            community_experience_fee_kobo=5_000_000,
        ),
        None,
        db,
    )
    assert confirm.await_args.kwargs["ticket_kind"] == "standard_member"
    assert confirm.await_args.kwargs["amount_kobo"] == 5_000_000
    purchase = next(
        call.args[0]
        for call in db.add.call_args_list
        if isinstance(call.args[0], clubs.CommunityExperiencePurchase)
    )
    assert purchase.price_context == "standard_member"
    assert purchase.amount_paid_kobo == 5_000_000
    assert application.status == "enrolled"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payment_mode,expected",
    [("transition_per_session", 5_000_000), ("quarterly_prepaid", 4_000_000)],
)
async def test_standalone_experience_only_discounts_prepaid_club(
    monkeypatch, payment_mode, expected
):
    statements = []
    product = SimpleNamespace(
        id=uuid4(),
        name="Q4 Experience",
        currency="NGN",
        is_active=True,
        purchase_opens_at=None,
        purchase_closes_at=None,
        period_start=date(2026, 10, 1),
        period_end=date(2026, 12, 31),
        standard_member_fee_kobo=5_000_000,
        club_member_fee_kobo=4_000_000,
    )

    async def execute(statement):
        statements.append(statement)
        if len(statements) == 2:
            # Exercise the actual query predicate against a transition/prepaid row.
            sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
            assert "payment_mode = 'quarterly_prepaid'" in sql
            return SimpleNamespace(
                first=lambda: (uuid4(),)
                if payment_mode == "quarterly_prepaid"
                else None
            )
        return SimpleNamespace(first=lambda: None, scalar_one_or_none=lambda: None)

    quote = await community_experiences._quote_for_member(
        SimpleNamespace(execute=execute),
        offering=product,
        member=SimpleNamespace(id=uuid4(), auth_id="AY"),
    )
    assert quote.amount_kobo == expected
