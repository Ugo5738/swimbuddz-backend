"""Contract tests for communications_service email API.

Other services (payments, academy, sessions) call the email API
to send emails. These tests verify the response shape.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
@pytest.mark.contract
async def test_email_send_response_contract(communications_client):
    """All services expect EmailResponse shape from /email/send."""
    with patch(
        "services.communications_service.routers.email.send_email",
        new_callable=AsyncMock,
        return_value=True,
    ):
        response = await communications_client.post(
            "/email/send",
            json={
                "to_email": "test@example.com",
                "subject": "Contract Test",
                "body": "Contract body",
            },
        )

    assert response.status_code == 200
    data = response.json()

    required_fields = ["success", "message", "sent_count", "failed_count"]
    for field in required_fields:
        assert field in data, (
            f"Missing contract field '{field}' in email response. "
            f"Used by payments_service and academy_service."
        )


@pytest.mark.asyncio
@pytest.mark.contract
async def test_email_template_response_contract(communications_client):
    """Template email returns same EmailResponse shape."""
    with patch(
        "services.communications_service.templates.members.send_member_approved_email",
        new_callable=AsyncMock,
        return_value=True,
    ):
        response = await communications_client.post(
            "/email/template",
            json={
                "template_type": "member_approved",
                "to_email": "test@example.com",
                "template_data": {"member_name": "Test"},
            },
        )

    assert response.status_code == 200
    data = response.json()

    required_fields = ["success", "message", "sent_count"]
    for field in required_fields:
        assert (
            field in data
        ), f"Missing contract field '{field}' in template email response."
