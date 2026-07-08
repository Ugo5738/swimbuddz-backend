"""Session notification trigger helpers.

Keep notification fan-out as a post-commit, best-effort concern for the
sessions service. The communications service remains the owner of email and
in-app delivery; this module just emits the existing "session-published"
service event from every code path that makes a session bookable.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import internal_post

logger = logging.getLogger(__name__)

SHORT_NOTICE_THRESHOLD_HOURS = 6


def is_short_notice_session(starts_at: datetime) -> bool:
    """Return whether a newly bookable session starts within the short-notice window."""
    return (starts_at - utc_now()).total_seconds() < (
        SHORT_NOTICE_THRESHOLD_HOURS * 3600
    )


async def trigger_session_published_notifications(
    *,
    session_id: uuid.UUID | str,
    starts_at: datetime,
    short_notice_message: str = "",
) -> bool:
    """Notify communications-service that a session is now bookable.

    Returns True when the service call succeeds. Failures are logged and
    swallowed so a committed session never turns into a user-facing 500 just
    because email delivery is temporarily unhealthy.
    """
    settings = get_settings()
    is_short_notice = is_short_notice_session(starts_at)

    try:
        await internal_post(
            service_url=settings.COMMUNICATIONS_SERVICE_URL,
            path="/internal/communications/session-published",
            calling_service="sessions",
            json={
                "session_id": str(session_id),
                "is_short_notice": is_short_notice,
                "short_notice_message": short_notice_message,
            },
        )
    except Exception as exc:
        logger.error(
            "Failed to trigger publish notifications for session %s: %s",
            session_id,
            exc,
        )
        return False

    return True
