"""Public-facing endpoints for the marketing site (no auth).

The lead-capture endpoint sits at ``POST /corporate/leads`` and is what
the marketing landing page at swimbuddz.com/corporate posts to. It is
rate-limited per IP, runs a honeypot check for naive bots, dedupes
inbound submissions by ``(company_name, primary_contact_email)`` against
the last 24h, and best-effort notifies admins via the communications
service so they can follow up within the playbook's 24h SLA.
"""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.rate_limit import limiter
from libs.common.service_client import dispatch_notification
from libs.db.session import get_async_db
from services.corporate_service.models import (
    ContactSource,
    CorporateContact,
    CorporateTouchpoint,
    TouchpointDirection,
    TouchpointType,
)
from services.corporate_service.schemas import (
    PublicLeadCreate,
    PublicLeadResponse,
)

logger = get_logger(__name__)
router = APIRouter(tags=["public-corporate"])


_LEAD_DEDUPE_WINDOW = timedelta(hours=24)


@router.post(
    "/leads",
    response_model=PublicLeadResponse,
    status_code=status.HTTP_201_CREATED,
)
@limiter.limit("5/minute")
async def create_public_lead(
    request: Request,
    payload: PublicLeadCreate,
    db: AsyncSession = Depends(get_async_db),
) -> PublicLeadResponse:
    """Capture an inbound corporate-wellness enquiry from the marketing site.

    Returns 201 with a generic acknowledgement regardless of whether we
    treated the submission as a duplicate or net-new — we don't want to
    leak "you already submitted this" to bots probing the endpoint, and
    legitimate users get the same friendly message either way.
    """
    # Honeypot trap — legit browsers leave this empty.
    if payload.website:
        logger.info(
            "corporate.leads honeypot tripped — silently accepting",
            extra={"email": payload.primary_contact_email},
        )
        return PublicLeadResponse()

    email_norm = payload.primary_contact_email.lower()
    cutoff = utc_now() - _LEAD_DEDUPE_WINDOW

    # Dedupe: a single email shouldn't be able to spawn endless contacts
    # by spamming the form. If a contact with this email was created in
    # the last 24h, attach a new touchpoint to it instead of a new contact.
    existing = (
        await db.execute(
            select(CorporateContact)
            .where(
                func.lower(CorporateContact.primary_contact_email) == email_norm,
                CorporateContact.created_at >= cutoff,
            )
            .order_by(CorporateContact.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing is not None:
        contact = existing
        is_new = False
    else:
        contact = CorporateContact(
            company_name=payload.company_name.strip(),
            primary_contact_name=payload.primary_contact_name.strip(),
            primary_contact_email=email_norm,
            source=ContactSource.INBOUND_WEB,
            notes=(
                f"Inbound web form — employee count guess: "
                f"{payload.employee_count or 'unspecified'}."
            ),
        )
        db.add(contact)
        await db.flush()  # need contact.id for the touchpoint
        is_new = True

    summary_bits = []
    if payload.employee_count:
        summary_bits.append(f"~{payload.employee_count} employees")
    if payload.message:
        summary_bits.append(payload.message.strip())
    summary = " · ".join(summary_bits) or "(no message)"

    touchpoint = CorporateTouchpoint(
        contact_id=contact.id,
        type=TouchpointType.NOTE,
        direction=TouchpointDirection.INBOUND,
        occurred_at=utc_now(),
        summary=summary[:500],
        outcome=("Inbound web form submission" if is_new else "Repeat submission (dedupe window)"),
    )
    db.add(touchpoint)
    await db.commit()

    # Best-effort: ping admins so they know a lead landed. The contact +
    # touchpoint are already persisted; a delivery failure here MUST NOT
    # cause the form to error out — that would lose the lead.
    try:
        await dispatch_notification(
            type="corporate_lead_received",
            category="ops",
            member_ids=[],  # Targeted by category at the admin layer.
            title=f"New corporate lead: {contact.company_name}",
            body=summary,
            calling_service="corporate_service",
            metadata={
                "contact_id": str(contact.id),
                "is_repeat_submission": not is_new,
                "employee_count": payload.employee_count,
            },
            channels=["in_app"],
        )
    except Exception:
        logger.warning(
            "Failed to notify admins of corporate lead (best-effort)",
            exc_info=True,
        )

    return PublicLeadResponse()


@router.get("/leads/health")
async def leads_health() -> dict:
    """Lightweight health hint for the marketing site to ping at page-load.

    Front-end can use this to gate the form's enabled state (e.g. show a
    fallback mailto: link if the API is down). Public so the marketing
    site can call it without any auth handshake.
    """
    return {"ok": True}
