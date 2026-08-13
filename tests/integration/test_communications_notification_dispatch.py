from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
@pytest.mark.integration
async def test_dispatch_email_uses_resolved_member_address_and_email_body(
    communications_client,
    monkeypatch,
):
    from libs.common.emails import core as email_core
    from services.communications_service.routers import notifications

    member_id = "a7816ad1-9d2f-4b39-92e9-9da0baa9176a"
    get_members = AsyncMock(
        return_value=[
            {
                "id": member_id,
                "auth_id": "auth-ada",
                "email": "ada@example.com",
            }
        ]
    )
    send_email = AsyncMock(return_value=True)
    monkeypatch.setattr(notifications, "get_members_bulk", get_members)
    monkeypatch.setattr(email_core, "send_email", send_email)

    response = await communications_client.post(
        "/notifications/dispatch",
        json={
            "type": "volunteer_of_the_month_winner",
            "category": "volunteer",
            "member_ids": [member_id],
            "title": "You're Volunteer of the Month",
            "body": "Congratulations, Ada!",
            "channels": ["in_app", "email"],
            "email_template": "volunteer_of_the_month",
            "email_data": {
                "body": "Plain-text congratulations",
                "html_content": "<h1>Congratulations</h1>",
            },
        },
    )

    assert response.status_code == 201
    send_email.assert_awaited_once_with(
        to_email="ada@example.com",
        subject="You're Volunteer of the Month",
        body="Plain-text congratulations",
        html_body="<h1>Congratulations</h1>",
    )


@pytest.mark.asyncio
@pytest.mark.integration
async def test_email_directory_failure_does_not_suppress_in_app_notification(
    communications_client,
    monkeypatch,
):
    from services.communications_service.routers import notifications

    member_id = "a7816ad1-9d2f-4b39-92e9-9da0baa9176a"
    monkeypatch.setattr(
        notifications,
        "get_members_bulk",
        AsyncMock(side_effect=RuntimeError("members service unavailable")),
    )

    response = await communications_client.post(
        "/notifications/dispatch",
        json={
            "type": "media_vault_access_granted",
            "category": "media",
            "member_ids": [member_id],
            "title": "Media vault assignment",
            "body": "You can now upload originals.",
            "channels": ["in_app", "email"],
            "email_template": "media_vault_access_granted",
        },
    )

    assert response.status_code == 201
    assert response.json() == {"dispatched": 1}
    listing = await communications_client.get(f"/notifications/?member_id={member_id}")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["type"] == "media_vault_access_granted"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_media_assignment_dispatch_uses_dedicated_branded_template(
    communications_client,
    monkeypatch,
):
    from services.communications_service.routers import notifications
    from services.communications_service.templates import media

    member_id = "a7816ad1-9d2f-4b39-92e9-9da0baa9176a"
    monkeypatch.setattr(
        notifications,
        "get_members_bulk",
        AsyncMock(
            return_value=[
                {
                    "id": member_id,
                    "auth_id": "auth-ada",
                    "email": "ada@example.com",
                    "first_name": "Ada",
                    "last_name": "Okafor",
                }
            ]
        ),
    )
    send_media_email = AsyncMock(return_value=True)
    monkeypatch.setattr(media, "send_media_vault_access_email", send_media_email)

    response = await communications_client.post(
        "/notifications/dispatch",
        json={
            "type": "media_vault_access_granted",
            "category": "media",
            "member_ids": [member_id],
            "title": "Media vault assignment: Saturday Swim",
            "body": "You can now upload originals.",
            "channels": ["in_app", "email"],
            "email_template": "media_vault_access_granted",
            "email_data": {
                "vault_title": "Saturday Swim",
                "role_label": "media uploader",
                "responsibility": "upload full-quality session media",
                "expires_at": "17 August 2026",
                "action_url": "https://www.swimbuddz.com/account/media-vault/vault-1",
            },
        },
    )

    assert response.status_code == 201
    send_media_email.assert_awaited_once_with(
        to_email="ada@example.com",
        member_name="Ada Okafor",
        vault_title="Saturday Swim",
        role_label="media uploader",
        responsibility="upload full-quality session media",
        expires_at="17 August 2026",
        action_url="https://www.swimbuddz.com/account/media-vault/vault-1",
    )
