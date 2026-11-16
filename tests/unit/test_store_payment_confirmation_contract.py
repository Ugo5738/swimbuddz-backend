"""Store payment confirmation is service-authenticated and non-regressive."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from services.store_service.models import OrderStatus
from services.store_service.routers.admin_inventory import _helpers
from services.store_service.routers.admin_inventory import orders as order_routes


def _service_user():
    return AuthUser(sub=str(uuid.uuid4()), role="service_role")


def _fake_db(order):
    result = SimpleNamespace(scalar_one_or_none=lambda: order)
    return SimpleNamespace(
        execute=AsyncMock(return_value=result),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_only_service_role_can_confirm_store_payment():
    with pytest.raises(HTTPException) as err:
        await require_service_role(
            AuthUser(sub=str(uuid.uuid4()), role="authenticated")
        )
    assert err.value.status_code == 403
    assert any(
        route.path == "/orders/{order_id}/mark-paid"
        for route in order_routes.router.routes
    )


@pytest.mark.asyncio
async def test_verified_payment_replay_preserves_collected_order(monkeypatch):
    order_id = uuid.uuid4()
    order = SimpleNamespace(
        id=order_id,
        status=OrderStatus.PICKED_UP,
        payment_reference="PAY-verified",
        paid_at=None,
        wallet_transaction_id=None,
    )
    db = _fake_db(order)
    audit = AsyncMock()
    monkeypatch.setattr(order_routes, "log_audit", audit)

    returned = await order_routes.confirm_verified_order_payment(
        order_id,
        payment_reference="PAY-verified",
        paid_at=None,
        wallet_transaction_id=None,
        current_user=_service_user(),
        db=db,
    )
    assert returned is order
    assert order.status == OrderStatus.PICKED_UP
    assert order.paid_at is not None
    db.commit.assert_awaited_once()
    audit.assert_awaited_once()

    await order_routes.confirm_verified_order_payment(
        order_id,
        payment_reference="PAY-verified",
        paid_at=None,
        wallet_transaction_id=None,
        current_user=_service_user(),
        db=db,
    )
    db.commit.assert_awaited_once()  # repeat confirmation is a no-op
    audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reference_mismatch_does_not_change_order():
    order_id = uuid.uuid4()
    order = SimpleNamespace(
        id=order_id,
        status=OrderStatus.PENDING_PAYMENT,
        payment_reference="PAY-actual",
    )
    db = _fake_db(order)
    with pytest.raises(HTTPException) as err:
        await order_routes.confirm_verified_order_payment(
            order_id,
            payment_reference="PAY-other",
            current_user=_service_user(),
            db=db,
        )
    assert err.value.status_code == 409
    assert order.status == OrderStatus.PENDING_PAYMENT
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_cannot_reopen_cancelled_order_with_payment_confirmation():
    order_id = uuid.uuid4()
    order = SimpleNamespace(
        id=order_id,
        status=OrderStatus.CANCELLED,
        payment_reference="PAY-real",
        wallet_transaction_id=None,
    )
    db = _fake_db(order)
    with pytest.raises(HTTPException) as err:
        await order_routes.confirm_verified_order_payment(
            order_id,
            payment_reference="PAY-real",
            current_user=_service_user(),
            db=db,
        )
    assert err.value.status_code == 409
    assert order.status == OrderStatus.CANCELLED
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_cannot_mark_unpaid_order_fulfilled():
    order = SimpleNamespace(status=OrderStatus.PENDING_PAYMENT)
    db = SimpleNamespace(commit=AsyncMock())
    with pytest.raises(HTTPException) as err:
        await _helpers._apply_order_status_change(
            db, order, OrderStatus.PICKED_UP, None, _service_user()
        )
    assert err.value.status_code == 409
    assert order.status == OrderStatus.PENDING_PAYMENT
    db.commit.assert_not_awaited()

@pytest.mark.asyncio
async def test_legacy_admin_mark_paid_shortcut_is_disabled():
    with pytest.raises(HTTPException) as err:
        await order_routes.mark_order_paid(
            uuid.uuid4(),
            current_user=_service_user(),
            db=SimpleNamespace(),
        )
    assert err.value.status_code == 410


def test_private_payment_confirmation_route_is_not_public_api_schema():
    from services.store_service.app.main import app

    private_routes = [
        route for route in app.routes
        if route.path == "/internal/store/orders/{order_id}/confirm-payment"
    ]
    assert len(private_routes) == 1
    assert private_routes[0].include_in_schema is False
    assert "/internal/store/orders/{order_id}/confirm-payment" not in app.openapi()["paths"]
