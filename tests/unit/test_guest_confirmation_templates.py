from unittest.mock import AsyncMock

import pytest

from services.communications_service.templates import guest_passes, sessions


@pytest.mark.asyncio
@pytest.mark.parametrize("post_session", [False, True])
async def test_guest_receipt_is_branded_and_lifecycle_aware(monkeypatch, post_session):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(guest_passes, "send_email", send)
    await guest_passes.send_guest_pass_confirmation_email(
        to_email="guest@example.com",
        guest_name="Ada <Guest>",
        session_title="Saturday swim",
        session_date="Saturday, 26 September 2026",
        session_time="10:00 – 12:00 (Africa/Lagos)",
        session_location="Rowe Park",
        session_address="Yaba",
        amount_paid=5000,
        payment_reference="GUEST-TEST",
        receipt_url="https://swimbuddz.com/guest-pass/id#token=private",
        post_session=post_session,
    )
    subject, text, html = send.await_args.args[1:]
    assert "swimbuddz-icon-white.png" in html
    for detail in [
        "Saturday swim",
        "Rowe Park",
        "GUEST-TEST",
        "5,000",
        "Africa/Lagos",
        "#token=private",
    ]:
        assert detail in text and detail in html
    assert "Ada &lt;Guest&gt;" in html
    assert "Ada <Guest>" not in html
    if post_session:
        assert "recorded" in subject
        assert "attendance separately" in text
        assert "See you" not in text + html
        assert "What to bring" not in html
    else:
        assert "confirmed" in subject
        assert "15 minutes" in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [None, "https://swimbuddz.com/guest-pass/session/id?ref=ADA&source=member_share"],
)
async def test_member_confirmation_includes_only_eligible_guest_link(monkeypatch, url):
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(sessions, "send_email", send)
    await sessions.send_session_confirmation_email(
        to_email="member@example.com",
        member_name="Ada",
        member_id="member",
        session_title="Swim",
        session_date="Saturday",
        session_time="10:00",
        session_location="Pool",
        guest_booking_url=url,
    )
    text, html = send.await_args.args[2:]
    assert ("Bring someone along" in html) == bool(url)
    if url:
        assert url in text
        assert "ref=ADA&amp;source=member_share" in html
