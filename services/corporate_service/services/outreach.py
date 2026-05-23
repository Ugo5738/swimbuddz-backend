"""Automated outreach engine.

The scheduler runs daily, scans all active CorporateContact rows that have
outreach started + not paused, and figures out who's due for their next
email. The send function:

  1. Re-checks state right before sending (race against admin pausing).
  2. Renders the playbook copy via outreach_templates.
  3. Sends via the centralised EmailClient.
  4. Logs a CorporateTouchpoint of the right type — this is what the
     scheduler reads next time to decide what to send.

Idempotency: the scheduler decides "what's due" by reading touchpoints,
not by writing locks. So a duplicate enqueue produces a duplicate email —
which would be noticeable. Real defence is to call send() only from the
scheduler (single producer) and short-circuit if the most recent
outreach touchpoint is < OUTREACH_GAP old.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.emails.client import EmailClient
from libs.common.logging import get_logger
from services.corporate_service.models import (
    CorporateContact,
    CorporateTouchpoint,
    TouchpointDirection,
    TouchpointType,
)
from services.corporate_service.services.outreach_templates import (
    OUTREACH_TYPES_IN_ORDER,
    OutreachEmail,
    next_email_number,
    render_email,
)

logger = get_logger(__name__)

# Gap between consecutive emails in the sequence. Matches the playbook
# (Day 1 → Day 7 → Day 14). Set on a 6.5-day floor so that "day 7" emails
# still fire if the cron runs at a slightly different time of day than
# the original send.
OUTREACH_GAP = timedelta(days=7)
_GAP_FLOOR = timedelta(days=6, hours=12)


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------


async def _most_recent_outreach_touchpoint(
    db: AsyncSession, contact_id
) -> Optional[CorporateTouchpoint]:
    rows = (
        (
            await db.execute(
                select(CorporateTouchpoint)
                .where(
                    CorporateTouchpoint.contact_id == contact_id,
                    CorporateTouchpoint.type.in_(OUTREACH_TYPES_IN_ORDER),
                    CorporateTouchpoint.direction == TouchpointDirection.OUTBOUND,
                )
                .order_by(CorporateTouchpoint.occurred_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


async def _last_inbound_touchpoint(
    db: AsyncSession, contact_id
) -> Optional[CorporateTouchpoint]:
    """If the contact replied (inbound touchpoint exists), the playbook
    says stop the sequence — they're now a real conversation, not a cold
    funnel."""
    rows = (
        (
            await db.execute(
                select(CorporateTouchpoint)
                .where(
                    CorporateTouchpoint.contact_id == contact_id,
                    CorporateTouchpoint.direction == TouchpointDirection.INBOUND,
                )
                .order_by(CorporateTouchpoint.occurred_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .all()
    )
    return rows[0] if rows else None


def _is_due(
    last_outbound: Optional[CorporateTouchpoint],
    started_at: Optional[datetime],
    now: datetime,
) -> bool:
    """Decide if the contact is due for their NEXT outreach email."""
    if last_outbound is None:
        # No outreach sent yet → first email is due as soon as outreach
        # is started. ``started_at`` must be set when an admin clicks
        # "Start sequence" — see admin_outreach.py.
        return started_at is not None and started_at <= now
    elapsed = now - last_outbound.occurred_at
    return elapsed >= _GAP_FLOOR


async def list_contacts_due_for_outreach(
    db: AsyncSession, *, now: Optional[datetime] = None
) -> list[CorporateContact]:
    """Find every contact whose next outreach email is due."""
    now = now or datetime.now(timezone.utc)

    candidates = (
        (
            await db.execute(
                select(CorporateContact).where(
                    CorporateContact.is_active.is_(True),
                    CorporateContact.outreach_paused.is_(False),
                    CorporateContact.outreach_started_at.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )

    due: list[CorporateContact] = []
    for contact in candidates:
        # Hard stop if they've replied at any point — turn off the
        # autopilot, let the admin take over.
        if await _last_inbound_touchpoint(db, contact.id) is not None:
            continue
        last = await _most_recent_outreach_touchpoint(db, contact.id)
        if last is not None and next_email_number(last.type) is None:
            # Sequence already exhausted (sent all 3).
            continue
        if _is_due(last, contact.outreach_started_at, now):
            due.append(contact)
    return due


# ---------------------------------------------------------------------------
# Send + log
# ---------------------------------------------------------------------------


async def send_next_outreach_email(
    db: AsyncSession,
    contact: CorporateContact,
    *,
    email_client: Optional[EmailClient] = None,
    dry_run: bool = False,
) -> Optional[CorporateTouchpoint]:
    """Send the next outreach email in the sequence for ``contact``.

    Re-checks state right before sending so we don't double-send when the
    scheduler races with an admin pausing or with another email landing
    inside the same tick. Returns the logged touchpoint, or None if there
    was nothing to send.
    """
    if contact.outreach_paused or contact.outreach_started_at is None:
        return None

    last = await _most_recent_outreach_touchpoint(db, contact.id)
    number = next_email_number(last.type if last else None)
    if number is None:
        return None

    if last is not None:
        elapsed = datetime.now(timezone.utc) - last.occurred_at
        if elapsed < _GAP_FLOOR:
            return None  # race-condition guard

    if await _last_inbound_touchpoint(db, contact.id) is not None:
        return None

    rendered: OutreachEmail = render_email(
        number,
        contact_name=contact.primary_contact_name,
        company_name=contact.company_name,
    )

    delivered = False
    if not dry_run:
        client = email_client or EmailClient()
        try:
            delivered = await client.send(
                to_email=contact.primary_contact_email,
                subject=rendered.subject,
                body=rendered.plain,
                html_body=rendered.html,
            )
        except Exception:
            logger.warning(
                "Outreach email send failed for contact %s (email %s)",
                contact.id,
                number,
                exc_info=True,
            )
            delivered = False

    touchpoint = CorporateTouchpoint(
        contact_id=contact.id,
        type=rendered.touchpoint_type,
        direction=TouchpointDirection.OUTBOUND,
        occurred_at=utc_now(),
        summary=(
            f"Automated outreach email {number} sent"
            if delivered
            else f"Automated outreach email {number} attempted (send failed)"
        )[:500],
        outcome=("Delivered" if delivered else "Send failed"),
    )
    db.add(touchpoint)
    await db.commit()
    await db.refresh(touchpoint)
    return touchpoint


async def run_outreach_cycle(db: AsyncSession) -> dict:
    """Top-level scheduler entrypoint. Used both by the ARQ cron and by
    the admin "run now" button."""
    sent = 0
    skipped = 0
    contacts = await list_contacts_due_for_outreach(db)
    for contact in contacts:
        result = await send_next_outreach_email(db, contact)
        if result is None:
            skipped += 1
        else:
            sent += 1
    return {"sent": sent, "skipped": skipped, "considered": len(contacts)}


# Re-exported for the admin preview endpoint.
def preview_email(
    number: int, *, contact_name: str, company_name: str
) -> OutreachEmail:
    """Render a template without sending. The admin preview UI calls this
    to show what each email will look like before kicking off the sequence."""
    return render_email(number, contact_name=contact_name, company_name=company_name)  # type: ignore[arg-type]


# Convenience for unit tests + admin debug.
def outreach_types() -> Sequence[TouchpointType]:
    return OUTREACH_TYPES_IN_ORDER
