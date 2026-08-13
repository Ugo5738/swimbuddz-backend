from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_media_vault_email_uses_branded_layout_and_escapes_content(monkeypatch):
    from services.communications_service.templates import media

    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(media, "send_email", send_email)

    result = await media.send_media_vault_access_email(
        to_email="ada@example.com",
        member_name="Ada <Admin>",
        vault_title="Saturday Swim & Social",
        role_label="media uploader",
        responsibility="upload full-quality session photos and videos",
        expires_at="Monday, 17 August 2026 at 12:00 UTC",
        action_url="https://www.swimbuddz.com/account/media-vault/vault-1",
    )

    assert result is True
    send_email.assert_awaited_once()
    to_email, subject, plain_body, html_body = send_email.await_args.args
    assert to_email == "ada@example.com"
    assert subject == "Media vault assignment: Saturday Swim & Social"
    assert "Open the media vault" in plain_body
    assert "<!DOCTYPE html>" in html_body
    assert "SwimBuddz" in html_body
    assert "Ada &lt;Admin&gt;" in html_body
    assert "Saturday Swim &amp; Social" in html_body
    assert "Open the media vault" in html_body
