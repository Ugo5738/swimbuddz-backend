"""Transactional emails for the PUBLIC Stroke Lab analyzer.

Sent from the WORKER on job completion/failure, routed through the shared
``EmailClient`` → communications_service (service-role auth, Brevo under the
hood). Best-effort: a send failure is logged and swallowed — it never affects
the job's terminal state (design §8.1/§8.6).
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
    """Email the guest a link to their finished analysis. Returns True on send."""
    url = _result_url(job_id, guest_token)
    subject = "Your SwimBuddz freestyle analysis is ready"
    body = (
        "Your freestyle stroke analysis is ready.\n\n"
        f"View it here: {url}\n\n"
        "— SwimBuddz Stroke Lab"
    )
    html_body = (
        "<p>Your freestyle stroke analysis is ready.</p>"
        f'<p><a href="{url}">View your analysis</a></p>'
        "<p style=\"color:#94a3b8;font-size:12px\">SwimBuddz Stroke Lab — a "
        "freestyle measurement tool, not a coach.</p>"
    )
    try:
        return await get_email_client().send(
            to_email=guest_email, subject=subject, body=body, html_body=html_body
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise
        logger.warning("ready email failed for job %s: %s", job_id, exc)
        return False


async def send_failed_email(job_id: uuid.UUID, guest_email: str) -> bool:
    """Email the guest that we couldn't analyze their clip (credit refunded)."""
    subject = "We couldn't analyze your freestyle clip"
    body = (
        "We couldn't analyze your clip this time, and your credit has been "
        "refunded.\n\n"
        "Tips: film side-on with the swimmer clearly in frame, 10–90 seconds, "
        "exported as MP4.\n\n"
        f"Try again: {ANALYZER_BASE_URL}\n\n"
        "— SwimBuddz Stroke Lab"
    )
    html_body = (
        "<p>We couldn't analyze your clip this time — and your credit has been "
        "<strong>refunded</strong>.</p>"
        "<p>Tips: film side-on with the swimmer clearly in frame, 10–90 seconds, "
        "exported as MP4.</p>"
        f'<p><a href="{ANALYZER_BASE_URL}">Try another clip</a></p>'
    )
    try:
        return await get_email_client().send(
            to_email=guest_email, subject=subject, body=body, html_body=html_body
        )
    except Exception as exc:  # noqa: BLE001 — best-effort, never raise
        logger.warning("failed-clip email failed for job %s: %s", job_id, exc)
        return False
