"""Cross-service read helpers for Member Swim Scorecard enrichments (ADR-026).

Every helper returns ``(payload_dict, True)`` on success or ``(None, False)``
on transport/HTTP failure so callers can degrade gracefully and surface explicit
``source_availability`` flags without fabricating zeros.
"""

from __future__ import annotations

from typing import Any, Optional, Tuple

from libs.common.config import get_settings
from libs.common.logging import get_logger

from .core import internal_get

logger = get_logger(__name__)

# Short timeout so a slow/offline sibling service never blocks the scorecard UI.
_SCORECARD_ENRICH_TIMEOUT = 4.0


async def _fetch_optional_json(
    *,
    service_url: str,
    path: str,
    calling_service: str,
    label: str,
) -> Tuple[Optional[dict[str, Any]], bool]:
    try:
        resp = await internal_get(
            service_url=service_url,
            path=path,
            calling_service=calling_service,
            timeout=_SCORECARD_ENRICH_TIMEOUT,
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data, True
        logger.warning(
            "Scorecard enrichment non-200 response",
            extra={
                "extra_fields": {
                    "source": label,
                    "status_code": resp.status_code,
                    "path": path,
                }
            },
        )
        return None, False
    except Exception as exc:
        logger.warning(
            "Scorecard enrichment unavailable (degrading gracefully)",
            extra={"extra_fields": {"source": label, "error": str(exc)}},
        )
        return None, False


async def fetch_academy_scorecard_summary(
    member_auth_id: str, *, calling_service: str = "members"
) -> Tuple[Optional[dict[str, Any]], bool]:
    settings = get_settings()
    return await _fetch_optional_json(
        service_url=settings.ACADEMY_SERVICE_URL,
        path=f"/internal/academy/members/{member_auth_id}/scorecard-summary",
        calling_service=calling_service,
        label="academy",
    )


async def fetch_strokelab_scorecard_summary(
    member_auth_id: str, *, calling_service: str = "members"
) -> Tuple[Optional[dict[str, Any]], bool]:
    settings = get_settings()
    return await _fetch_optional_json(
        service_url=settings.AI_SERVICE_URL,
        path=f"/internal/ai/members/{member_auth_id}/strokelab-summary",
        calling_service=calling_service,
        label="strokelab",
    )


async def fetch_reporting_scorecard_summary(
    member_auth_id: str, *, calling_service: str = "members"
) -> Tuple[Optional[dict[str, Any]], bool]:
    settings = get_settings()
    return await _fetch_optional_json(
        service_url=settings.REPORTING_SERVICE_URL,
        path=f"/internal/reporting/members/{member_auth_id}/scorecard-summary",
        calling_service=calling_service,
        label="reporting",
    )


async def fetch_volunteer_scorecard_summary(
    member_id: str, *, calling_service: str = "members"
) -> Tuple[Optional[dict[str, Any]], bool]:
    settings = get_settings()
    return await _fetch_optional_json(
        service_url=settings.VOLUNTEER_SERVICE_URL,
        path=f"/internal/volunteer/members/{member_id}/scorecard-summary",
        calling_service=calling_service,
        label="volunteer",
    )
