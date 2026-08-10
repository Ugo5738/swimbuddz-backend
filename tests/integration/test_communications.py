"""Integration tests for communications_service endpoints."""

from unittest.mock import AsyncMock, patch

import pytest

from services.communications_service.models import NotificationPreferences
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
async def test_send_single_email_honors_declared_preference_category(
    communications_client, db_session
):
    db_session.add(
        NotificationPreferences(
            member_auth_id="auth-payment-opt-out",
            email_payment_receipts=False,
        )
    )
    await db_session.commit()

    sender = AsyncMock(return_value=True)
    with (
        patch(
            "services.communications_service.routers.email.search_members",
            new_callable=AsyncMock,
            return_value=[
                {
                    "id": "22222222-2222-2222-2222-222222222222",
                    "auth_id": "auth-payment-opt-out",
                    "email": "payer@example.com",
                }
            ],
        ),
        patch("services.communications_service.routers.email.send_email", sender),
    ):
        response = await communications_client.post(
            "/email/send",
            json={
                "to_email": "payer@example.com",
                "subject": "Membership renewal",
                "body": "Your membership is due for renewal.",
                "preference_category": "payments",
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["sent_count"] == 0
    sender.assert_not_awaited()


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


@pytest.mark.asyncio
@pytest.mark.integration
async def test_send_templated_email_honors_member_preference(
    communications_client, db_session
):
    """Member-facing templates are suppressed centrally after an exact email match."""
    db_session.add(
        NotificationPreferences(
            member_auth_id="auth-academy-opt-out",
            email_academy_updates=False,
        )
    )
    await db_session.commit()

    sender = AsyncMock(return_value=True)
    with (
        patch(
            "services.communications_service.routers.email.search_members",
            new_callable=AsyncMock,
            return_value=[
                {
                    "id": "11111111-1111-1111-1111-111111111111",
                    "auth_id": "auth-academy-opt-out",
                    "email": "student@example.com",
                }
            ],
        ),
        patch(
            "services.communications_service.templates.academy.send_enrollment_confirmation_email",
            sender,
        ),
    ):
        response = await communications_client.post(
            "/email/template",
            json={
                "template_type": "enrollment_confirmation",
                "to_email": "student@example.com",
                "template_data": {},
            },
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["sent_count"] == 0
    assert "suppressed" in response.json()["message"]
    sender.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_direct_coach_message_honors_member_preference(
    communications_client, db_session
):
    db_session.add(
        NotificationPreferences(
            member_auth_id="auth-coach-message-opt-out",
            email_coach_messages=False,
        )
    )
    await db_session.commit()
    enrollment_id = "33333333-3333-3333-3333-333333333333"
    sender = AsyncMock(return_value=True)

    with (
        patch(
            "services.communications_service.routers.messaging.get_member_id_from_auth",
            new_callable=AsyncMock,
            return_value="44444444-4444-4444-4444-444444444444",
        ),
        patch(
            "services.communications_service.routers.messaging.get_enrollment_student",
            new_callable=AsyncMock,
            return_value={
                "enrollment_id": enrollment_id,
                "member_id": "55555555-5555-5555-5555-555555555555",
                "cohort_id": "66666666-6666-6666-6666-666666666666",
                "auth_id": "auth-coach-message-opt-out",
                "email": "student@example.com",
            },
        ),
        patch(
            "services.communications_service.routers.messaging.send_message_email",
            sender,
        ),
    ):
        response = await communications_client.post(
            f"/messages/enrollments/{enrollment_id}",
            json={"subject": "Training update", "body": "Please review your drills."},
        )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["recipients_count"] == 0
    sender.assert_not_awaited()
