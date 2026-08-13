import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_full_bubbles_booking_sends_standard_confirmation(monkeypatch):
    from services.sessions_service.routers import bookings

    member_id = uuid.uuid4()
    get_member = AsyncMock(
        return_value={
            "id": str(member_id),
            "email": "ada@example.com",
            "first_name": "Ada",
            "last_name": "Okafor",
        }
    )
    email_client = SimpleNamespace(send_template=AsyncMock(return_value=True))
    monkeypatch.setattr(bookings, "get_member_by_auth_id", get_member)
    monkeypatch.setattr(bookings, "get_email_client", lambda: email_client)
    session = SimpleNamespace(
        id=uuid.uuid4(),
        title="Saturday Club Swim",
        timezone="Africa/Lagos",
        starts_at=datetime(2026, 8, 15, 8, tzinfo=timezone.utc),
        ends_at=datetime(2026, 8, 15, 11, tzinfo=timezone.utc),
        location_name="Rowe Park",
        location=None,
        location_address="Yaba, Lagos",
    )

    await bookings._send_direct_booking_confirmation(
        current_user=SimpleNamespace(user_id="auth-ada"),
        member_id=member_id,
        session=session,
        fee_amount_kobo=350_000,
        paid_with_bubbles=True,
    )

    email_client.send_template.assert_awaited_once()
    kwargs = email_client.send_template.await_args.kwargs
    assert kwargs["template_type"] == "session_confirmation"
    assert kwargs["to_email"] == "ada@example.com"
    assert kwargs["template_data"]["member_name"] == "Ada Okafor"
    assert kwargs["template_data"]["bubbles_applied"] == 35
    assert kwargs["template_data"]["amount_paid"] == 3500
