import uuid
from urllib.parse import urlparse

import pytest

from services.communications_service.models import WeeklyDigestDispatch
from tests.factories import ContentPostFactory


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize(
    ("tier_access", "expected_path_prefix"),
    [("community", "/tips/"), ("club", "/community/tips/")],
)
async def test_digest_article_redirect_is_tier_aware(
    communications_client,
    db_session,
    tier_access,
    expected_path_prefix,
):
    post = ContentPostFactory.create(tier_access=tier_access, is_published=True)
    dispatch = WeeklyDigestDispatch(
        campaign_key=f"2026-W32-{tier_access}",
        member_id=uuid.uuid4(),
        recipient_email="member@example.com",
        tracking_token=uuid.uuid4(),
        delivery_status="sent",
    )
    db_session.add_all([post, dispatch])
    await db_session.commit()

    response = await communications_client.get(
        f"/digest/click/{dispatch.tracking_token}/article/{post.id}",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert (
        urlparse(response.headers["location"]).path
        == f"{expected_path_prefix}{post.id}"
    )
    assert "source=weekly_digest" in response.headers["location"]
