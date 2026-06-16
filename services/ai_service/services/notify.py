"""Transactional emails for the PUBLIC Stroke Lab analyzer.

Sent from the WORKER on job completion/failure, routed through the shared
``EmailClient`` → communications_service, which renders the BRANDED SwimBuddz
template (``analyzer_ready`` / ``analyzer_failed``) and delivers via Brevo.
Best-effort: a send failure is logged and swallowed — it never affects the
job's terminal state (design §8.1/§8.6).
"""

from __future__ import annotations

import os
import uuid
from urllib.parse import quote

from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger

logger = get_logger(__name__)

ANALYZER_BASE_URL = os.environ.get(
    "ANALYZER_BASE_URL", "https://analyzer.swimbuddz.com"
).rstrip("/")


def _result_url(job_id: uuid.UUID, guest_token: str) -> str:
    # The per-job guest_token is the bearer capability. Referrer-Policy:no-referrer
    # on the analyzer site keeps it from leaking via Referer; a magic-JWT + cookie
    # swap (design §5.3) is a later hardening.
    return f"{ANALYZER_BASE_URL}/r/{job_id}?guest_token={quote(guest_token, safe='')}"


async def send_ready_email(
    job_id: uuid.UUID, guest_email: str, guest_token: str
) -> bool:
    """Email the guest a link to their finished analysis. Returns True on send.

    Renders the branded ``analyzer_ready`` template in communications_service
    (subject + HTML live there, not here).
    """
    url = _result_url(job_id, guest_token)
    try:
        return await get_email_client().send_template(
            template_type="analyzer_ready",
            to_email=guest_email,
            template_data={"result_url": url},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise
        logger.warning("ready email failed for job %s: %s", job_id, exc)
        return False


async def send_failed_email(job_id: uuid.UUID, guest_email: str) -> bool:
    """Email the guest that we couldn't analyze their clip (credit refunded).

    Renders the branded ``analyzer_failed`` template in communications_service.
    """
    try:
        return await get_email_client().send_template(
            template_type="analyzer_failed",
            to_email=guest_email,
            template_data={"retry_url": ANALYZER_BASE_URL},
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise
        logger.warning("failed-clip email failed for job %s: %s", job_id, exc)
        return False
