"""High-level helpers for the communications service."""

from __future__ import annotations

from typing import Optional

from libs.common.config import get_settings
from libs.common.logging import get_logger

from .core import internal_post

logger = get_logger(__name__)


async def dispatch_notification(
    *,
    type: str,
    category: str,
    member_ids: list[str],
    title: str,
    calling_service: str,
    body: Optional[str] = None,
    action_url: Optional[str] = None,
    icon: Optional[str] = None,
    metadata: Optional[dict] = None,
    channels: Optional[list[str]] = None,
    email_template: Optional[str] = None,
    email_data: Optional[dict] = None,
    expires_at: Optional[str] = None,
) -> Optional[dict]:
    """Dispatch a personal notification via the communications service.

    Best-effort: catches all exceptions so the calling operation is never
    blocked by notification delivery.

    Returns dict with {dispatched: int} on success, None on failure.
    """
    if not member_ids:
        return None
    settings = get_settings()
    try:
        resp = await internal_post(
            service_url=settings.COMMUNICATIONS_SERVICE_URL,
            path="/notifications/dispatch",
            calling_service=calling_service,
            json={
                "type": type,
                "category": category,
                "member_ids": member_ids,
                "title": title,
                "body": body,
                "action_url": action_url,
                "icon": icon,
                "metadata": metadata,
                "channels": channels or ["in_app"],
                "email_template": email_template,
                "email_data": email_data,
                "expires_at": expires_at,
            },
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        logger.warning(
            "Failed to dispatch notification %s for %s (best-effort, continuing)",
            type,
            member_ids,
            exc_info=True,
        )
        return None
