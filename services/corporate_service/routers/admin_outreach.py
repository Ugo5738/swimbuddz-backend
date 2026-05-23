"""Admin controls for the automated outreach sequence.

Endpoints:
    GET    /admin/corporate/contacts/{id}/outreach           — current state
    POST   /admin/corporate/contacts/{id}/outreach/start     — kick off
    POST   /admin/corporate/contacts/{id}/outreach/pause     — pause
    POST   /admin/corporate/contacts/{id}/outreach/resume    — resume
    GET    /admin/corporate/contacts/{id}/outreach/preview   — see all 3 emails
    POST   /admin/corporate/contacts/{id}/outreach/send-now  — fire next email
    POST   /admin/corporate/outreach/run-cycle               — global tick

The scheduler runs daily on the ARQ worker; the manual run-cycle endpoint
exists so admins can sanity-check end-to-end during onboarding and so
ops can recover from missed cron ticks.
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.corporate_service.models import (
    CorporateContact,
    CorporateTouchpoint,
    TouchpointDirection,
)
from services.corporate_service.schemas import (
    OutreachCycleResult,
    OutreachPreviewResponse,
    OutreachSendResult,
    OutreachStartRequest,
    OutreachStateResponse,
)
from services.corporate_service.services.outreach import (
    run_outreach_cycle,
    send_next_outreach_email,
)
from services.corporate_service.services.outreach_templates import (
    OUTREACH_TYPES_IN_ORDER,
    next_email_number,
    render_email,
)

router = APIRouter(tags=["admin-corporate-outreach"])


async def _load_contact(
    db: AsyncSession, contact_id: uuid.UUID
) -> CorporateContact:
    contact = (
        await db.execute(
            select(CorporateContact).where(CorporateContact.id == contact_id)
        )
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(status_code=404, detail="Corporate contact not found")
    return contact


async def _outreach_state(
    db: AsyncSession, contact: CorporateContact
) -> OutreachStateResponse:
    last = (
        (
            await db.execute(
                select(CorporateTouchpoint)
                .where(
                    CorporateTouchpoint.contact_id == contact.id,
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
    last_tp = last[0] if last else None

    has_inbound = (
        (
            await db.execute(
                select(CorporateTouchpoint.id)
                .where(
                    CorporateTouchpoint.contact_id == contact.id,
                    CorporateTouchpoint.direction == TouchpointDirection.INBOUND,
                )
                .limit(1)
            )
        ).scalar_one_or_none()
    ) is not None

    return OutreachStateResponse(
        contact_id=contact.id,
        outreach_paused=contact.outreach_paused,
        outreach_started_at=contact.outreach_started_at,
        last_outbound_email_at=last_tp.occurred_at if last_tp else None,
        last_outbound_email_type=last_tp.type.value if last_tp else None,
        next_email_number=next_email_number(last_tp.type if last_tp else None),
        has_inbound_reply=has_inbound,
    )


@router.get(
    "/contacts/{contact_id}/outreach",
    response_model=OutreachStateResponse,
)
async def get_outreach_state(
    contact_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    contact = await _load_contact(db, contact_id)
    return await _outreach_state(db, contact)


@router.post(
    "/contacts/{contact_id}/outreach/start",
    response_model=OutreachStateResponse,
)
async def start_outreach(
    contact_id: uuid.UUID,
    _payload: OutreachStartRequest | None = None,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Kick off the 3-email sequence. The next scheduler tick (or a manual
    /send-now) will pick this up immediately."""
    contact = await _load_contact(db, contact_id)
    if contact.outreach_started_at is None:
        contact.outreach_started_at = utc_now()
    contact.outreach_paused = False
    await db.commit()
    await db.refresh(contact)
    return await _outreach_state(db, contact)


@router.post(
    "/contacts/{contact_id}/outreach/pause",
    response_model=OutreachStateResponse,
)
async def pause_outreach(
    contact_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    contact = await _load_contact(db, contact_id)
    contact.outreach_paused = True
    await db.commit()
    await db.refresh(contact)
    return await _outreach_state(db, contact)


@router.post(
    "/contacts/{contact_id}/outreach/resume",
    response_model=OutreachStateResponse,
)
async def resume_outreach(
    contact_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    contact = await _load_contact(db, contact_id)
    contact.outreach_paused = False
    if contact.outreach_started_at is None:
        contact.outreach_started_at = utc_now()
    await db.commit()
    await db.refresh(contact)
    return await _outreach_state(db, contact)


@router.get(
    "/contacts/{contact_id}/outreach/preview",
    response_model=List[OutreachPreviewResponse],
)
async def preview_outreach(
    contact_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Render all 3 emails for this contact so admins can review the copy
    before kicking off the sequence."""
    contact = await _load_contact(db, contact_id)
    out: list[OutreachPreviewResponse] = []
    for n in (1, 2, 3):
        rendered = render_email(
            n,  # type: ignore[arg-type]
            contact_name=contact.primary_contact_name,
            company_name=contact.company_name,
        )
        out.append(
            OutreachPreviewResponse(
                number=n,
                subject=rendered.subject,
                plain=rendered.plain,
                html=rendered.html,
            )
        )
    return out


@router.post(
    "/contacts/{contact_id}/outreach/send-now",
    response_model=OutreachSendResult,
)
async def send_now(
    contact_id: uuid.UUID,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Force-send the next email in the sequence right now.

    Honours pause / paused-by-reply / sequence-exhausted guards — so it's
    safe to click without re-checking state first. Returns ``sent=false``
    with a human-readable reason when nothing was due.
    """
    contact = await _load_contact(db, contact_id)
    if contact.outreach_paused:
        return OutreachSendResult(sent=False, reason="Outreach is paused for this contact")
    if contact.outreach_started_at is None:
        return OutreachSendResult(
            sent=False, reason="Outreach hasn't been started yet"
        )
    touchpoint = await send_next_outreach_email(db, contact)
    if touchpoint is None:
        return OutreachSendResult(sent=False, reason="No outreach email is due")
    return OutreachSendResult(
        sent=True,
        email_number=(
            1
            if touchpoint.type.value == "email_intro"
            else 2
            if touchpoint.type.value == "email_followup_1"
            else 3
        ),
        touchpoint_id=touchpoint.id,
    )


@router.post(
    "/outreach/run-cycle",
    response_model=OutreachCycleResult,
    status_code=status.HTTP_200_OK,
)
async def run_cycle(
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Manually trigger one tick of the outreach scheduler.

    Sends the next-due email to every contact whose gap is up. Idempotent
    on minutes — running twice in a row will only fire once because the
    gap floor is days.
    """
    result = await run_outreach_cycle(db)
    return OutreachCycleResult(**result)
