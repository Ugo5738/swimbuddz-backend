from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.payments_service.services import academy_pricing


class _Response:
    status_code = 200
    text = "ok"

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _AsyncClientContext:
    def __init__(self, response):
        self.get = AsyncMock(return_value=response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _enrollment_payload(**overrides):
    payload = {
        "id": str(uuid4()),
        "member_id": str(uuid4()),
        "cohort_id": str(uuid4()),
        "status": "enrolled",
        "payment_status": "pending",
        "price_snapshot_amount": 15_000_000,
        "currency_snapshot": "NGN",
        "installments": [],
        "program": {
            "price_amount": 200_000,
            "membership_policy": "open",
        },
        "cohort": {
            "price_override": 180_000,
            "membership_policy_override": None,
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot, expected", [(15_000_000, 15_000_000), (None, 18_000_000)]
)
async def test_full_payment_uses_snapshot_or_genuine_legacy_fallback(
    monkeypatch, snapshot, expected
):
    enrollment = _enrollment_payload(price_snapshot_amount=snapshot)
    client = _AsyncClientContext(_Response(enrollment))
    monkeypatch.setattr(
        academy_pricing.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )
    monkeypatch.setattr(
        academy_pricing,
        "get_member_by_auth_id",
        AsyncMock(return_value={"id": enrollment["member_id"], "membership": {}}),
    )

    context = await academy_pricing.academy_payment_context(
        enrollment_id=uuid4(),
        member_auth_id="member-auth",
        use_installments=False,
    )

    assert context["academy_amount_kobo"] == expected
    assert context["subtotal_kobo"] == expected


@pytest.mark.asyncio
async def test_waitlisted_enrollment_is_not_payable(monkeypatch):
    enrollment = _enrollment_payload(status="waitlist")
    client = _AsyncClientContext(_Response(enrollment))
    monkeypatch.setattr(
        academy_pricing.httpx,
        "AsyncClient",
        lambda **_kwargs: client,
    )
    monkeypatch.setattr(
        academy_pricing,
        "get_member_by_auth_id",
        AsyncMock(return_value={"id": enrollment["member_id"], "membership": {}}),
    )

    with pytest.raises(HTTPException) as exc_info:
        await academy_pricing.academy_payment_context(
            enrollment_id=uuid4(),
            member_auth_id="member-auth",
            use_installments=True,
        )

    assert exc_info.value.status_code == 409
    assert "waitlisted" in exc_info.value.detail
