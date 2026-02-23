"""Integration tests for communications_service endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from tests.factories import AnnouncementFactory

# ---------------------------------------------------------------------------
# Announcements - list (public, no external calls needed with include_all)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_announcements_admin(communications_client, db_session):
    """Admin can list all announcements including drafts."""
    ann = AnnouncementFactory.create()
    db_session.add(ann)
    await db_session.commit()

    response = await communications_client.get(
        "/announcements/",
        params={"include_all": "true"},
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


# ---------------------------------------------------------------------------
# Email API (service-to-service, require_service_role)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_send_single_email(communications_client):
    """Service-to-service email send endpoint works with mocked sender."""
    with patch(
        "services.communications_service.routers.email.send_email",
        new_callable=AsyncMock,
        return_value=True,
    ):
        response = await communications_client.post(
            "/email/send",
            json={
                "to_email": "test@example.com",
                "subject": "Test Subject",
                "body": "Test body",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["sent_count"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_send_single_email_failure(communications_client):
    """Returns failed_count when email send fails."""
    with patch(
        "services.communications_service.routers.email.send_email",
        new_callable=AsyncMock,
        return_value=False,
    ):
        response = await communications_client.post(
            "/email/send",
            json={
                "to_email": "test@example.com",
                "subject": "Test Subject",
                "body": "Test body",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["failed_count"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_send_templated_email_unknown_template(communications_client):
    """Returns 400 for unknown template type."""
    response = await communications_client.post(
        "/email/template",
        json={
            "template_type": "nonexistent_template",
            "to_email": "test@example.com",
            "template_data": {},
        },
    )

    assert response.status_code == 400
    assert "Unknown template type" in response.json()["detail"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_send_templated_email_valid(communications_client):
    """Templated email endpoint works with a valid template type."""
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
                "template_data": {"member_name": "John Doe"},
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
