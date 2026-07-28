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
