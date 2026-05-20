"""Admin: volunteer template CRUD.

Two template surfaces:

* ``SessionTemplateVolunteerSlot`` — child of a session template. CRUD
  endpoints are scoped under ``/session-templates/{template_id}/slots``.

* ``VolunteerOpportunityTemplate`` — standalone recurring opportunity
  not tied to a session. CRUD under ``/opportunity-templates``.

See docs/design/VOLUNTEER_OPPORTUNITY_CONTEXT_DESIGN.md.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.volunteer_service.models import (
    OpportunityStatus,
    SessionTemplateVolunteerSlot,
    VolunteerOpportunity,
    VolunteerOpportunityTemplate,
)
from services.volunteer_service.schemas import (
    MaterialiseTemplateRequest,
    MaterialiseTemplateResponse,
    SessionTemplateVolunteerSlotCreate,
    SessionTemplateVolunteerSlotResponse,
    SessionTemplateVolunteerSlotUpdate,
    VolunteerOpportunityTemplateCreate,
    VolunteerOpportunityTemplateResponse,
    VolunteerOpportunityTemplateUpdate,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Enrichment helpers
# ---------------------------------------------------------------------------


def _enrich_st_slot(slot: SessionTemplateVolunteerSlot) -> dict:
    """Attach role title/category for the admin UI."""
    base = SessionTemplateVolunteerSlotResponse.model_validate(slot).model_dump()
    if slot.role is not None:
        base["role_title"] = slot.role.title
        base["role_category"] = slot.role.category.value
    return base


def _enrich_opp_template(t: VolunteerOpportunityTemplate) -> dict:
    base = VolunteerOpportunityTemplateResponse.model_validate(t).model_dump()
    if t.role is not None:
        base["role_title"] = t.role.title
        base["role_category"] = t.role.category.value
    return base


# ---------------------------------------------------------------------------
# SessionTemplateVolunteerSlot CRUD — keyed by parent session_template_id
# ---------------------------------------------------------------------------


@router.get(
    "/session-templates/{session_template_id}/slots",
    response_model=list[SessionTemplateVolunteerSlotResponse],
)
async def list_session_template_slots(
    session_template_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """List volunteer-need rows attached to a session template."""
    rows = (
        (
            await db.execute(
                select(SessionTemplateVolunteerSlot)
                .options(selectinload(SessionTemplateVolunteerSlot.role))
                .where(
                    SessionTemplateVolunteerSlot.session_template_id
                    == session_template_id
                )
                .order_by(SessionTemplateVolunteerSlot.created_at.asc())
            )
        )
        .scalars()
        .all()
    )
    return [_enrich_st_slot(r) for r in rows]


@router.post(
    "/session-templates/{session_template_id}/slots",
    response_model=SessionTemplateVolunteerSlotResponse,
    status_code=201,
)
async def create_session_template_slot(
    session_template_id: uuid.UUID,
    data: SessionTemplateVolunteerSlotCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """Attach a volunteer-need row to a session template.

    The ``session_template_id`` in the path is authoritative — any value
    in the request body is overwritten with it (so the same request body
    can't be replayed against a different template).
    """
    payload = data.model_dump()
    payload["session_template_id"] = session_template_id
    slot = SessionTemplateVolunteerSlot(**payload)
    db.add(slot)
    await db.commit()
    slot = (
        await db.execute(
            select(SessionTemplateVolunteerSlot)
            .options(selectinload(SessionTemplateVolunteerSlot.role))
            .where(SessionTemplateVolunteerSlot.id == slot.id)
        )
    ).scalar_one()
    return _enrich_st_slot(slot)


@router.patch(
    "/session-templates/{session_template_id}/slots/{slot_id}",
    response_model=SessionTemplateVolunteerSlotResponse,
)
async def update_session_template_slot(
    session_template_id: uuid.UUID,
    slot_id: uuid.UUID,
    data: SessionTemplateVolunteerSlotUpdate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(
            select(SessionTemplateVolunteerSlot)
            .options(selectinload(SessionTemplateVolunteerSlot.role))
            .where(
                SessionTemplateVolunteerSlot.id == slot_id,
                SessionTemplateVolunteerSlot.session_template_id == session_template_id,
            )
        )
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(slot, field, value)
    slot.updated_at = utc_now()
    await db.commit()
    await db.refresh(slot)
    return _enrich_st_slot(slot)


@router.delete(
    "/session-templates/{session_template_id}/slots/{slot_id}",
    status_code=204,
)
async def delete_session_template_slot(
    session_template_id: uuid.UUID,
    slot_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    slot = (
        await db.execute(
            select(SessionTemplateVolunteerSlot).where(
                SessionTemplateVolunteerSlot.id == slot_id,
                SessionTemplateVolunteerSlot.session_template_id == session_template_id,
            )
        )
    ).scalar_one_or_none()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    await db.delete(slot)
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# Standalone VolunteerOpportunityTemplate CRUD
# ---------------------------------------------------------------------------


@router.get(
    "/opportunity-templates",
    response_model=list[VolunteerOpportunityTemplateResponse],
)
async def list_opportunity_templates(
    admin: Annotated[AuthUser, Depends(require_admin)],
    active_only: bool = False,
    db: AsyncSession = Depends(get_async_db),
):
    q = (
        select(VolunteerOpportunityTemplate)
        .options(selectinload(VolunteerOpportunityTemplate.role))
        .order_by(
            VolunteerOpportunityTemplate.day_of_week.asc(),
            VolunteerOpportunityTemplate.start_time.asc(),
        )
    )
    if active_only:
        q = q.where(VolunteerOpportunityTemplate.is_active.is_(True))
    rows = (await db.execute(q)).scalars().all()
    return [_enrich_opp_template(r) for r in rows]


@router.post(
    "/opportunity-templates",
    response_model=VolunteerOpportunityTemplateResponse,
    status_code=201,
)
async def create_opportunity_template(
    data: VolunteerOpportunityTemplateCreate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    tmpl = VolunteerOpportunityTemplate(**data.model_dump())
    db.add(tmpl)
    await db.commit()
    tmpl = (
        await db.execute(
            select(VolunteerOpportunityTemplate)
            .options(selectinload(VolunteerOpportunityTemplate.role))
            .where(VolunteerOpportunityTemplate.id == tmpl.id)
        )
    ).scalar_one()
    return _enrich_opp_template(tmpl)


@router.patch(
    "/opportunity-templates/{template_id}",
    response_model=VolunteerOpportunityTemplateResponse,
)
async def update_opportunity_template(
    template_id: uuid.UUID,
    data: VolunteerOpportunityTemplateUpdate,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    tmpl = (
        await db.execute(
            select(VolunteerOpportunityTemplate)
            .options(selectinload(VolunteerOpportunityTemplate.role))
            .where(VolunteerOpportunityTemplate.id == template_id)
        )
    ).scalar_one_or_none()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(tmpl, field, value)
    tmpl.updated_at = utc_now()
    await db.commit()
    await db.refresh(tmpl)
    return _enrich_opp_template(tmpl)


@router.delete("/opportunity-templates/{template_id}", status_code=204)
async def delete_opportunity_template(
    template_id: uuid.UUID,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    tmpl = (
        await db.execute(
            select(VolunteerOpportunityTemplate).where(
                VolunteerOpportunityTemplate.id == template_id
            )
        )
    ).scalar_one_or_none()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    await db.delete(tmpl)
    await db.commit()
    return None


# ---------------------------------------------------------------------------
# Materialise standalone template into concrete opportunities
# ---------------------------------------------------------------------------


def _next_occurrence(start: date, day_of_week: int) -> date:
    """Return the first date >= start whose weekday matches day_of_week."""
    delta = (day_of_week - start.weekday()) % 7
    return start + timedelta(days=delta)


@router.post(
    "/opportunity-templates/{template_id}/materialise",
    response_model=MaterialiseTemplateResponse,
)
async def materialise_opportunity_template(
    template_id: uuid.UUID,
    body: MaterialiseTemplateRequest,
    admin: Annotated[AuthUser, Depends(require_admin)],
    db: AsyncSession = Depends(get_async_db),
):
    """Generate concrete opportunities from a standalone template.

    Walks weekly from ``max(last_materialised_through + 1, today)`` to
    ``through_date`` inclusive, creating one VolunteerOpportunity per
    matching weekday. Skips dates that already have an opportunity for
    this template's role at the same time (cheap defence against double
    materialisation if the admin clicks twice).
    """
    tmpl = (
        await db.execute(
            select(VolunteerOpportunityTemplate).where(
                VolunteerOpportunityTemplate.id == template_id
            )
        )
    ).scalar_one_or_none()
    if not tmpl:
        raise HTTPException(status_code=404, detail="Template not found")
    if not tmpl.is_active:
        raise HTTPException(
            status_code=400, detail="Template is inactive; activate it first."
        )

    today = date.today()
    cursor_start = today
    if tmpl.last_materialised_through and tmpl.last_materialised_through >= today:
        cursor_start = tmpl.last_materialised_through + timedelta(days=1)

    if body.through_date < cursor_start:
        return MaterialiseTemplateResponse(
            success=True,
            created_count=0,
            last_materialised_through=tmpl.last_materialised_through or today,
        )

    cursor = _next_occurrence(cursor_start, tmpl.day_of_week)
    created = 0
    while cursor <= body.through_date:
        # Idempotency: skip if an opportunity already exists for this
        # template's role on this date at this start time. The lookup is
        # narrow enough that an admin double-click won't double-create,
        # without needing a unique index.
        existing = (
            await db.execute(
                select(VolunteerOpportunity.id).where(
                    VolunteerOpportunity.role_id == tmpl.role_id,
                    VolunteerOpportunity.date == cursor,
                    VolunteerOpportunity.start_time == tmpl.start_time,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            end_time: Optional[time] = None
            if tmpl.start_time and tmpl.duration_minutes:
                end_dt = datetime.combine(cursor, tmpl.start_time) + timedelta(
                    minutes=tmpl.duration_minutes
                )
                end_time = end_dt.time()
            opp = VolunteerOpportunity(
                title=tmpl.title,
                description=tmpl.description,
                role_id=tmpl.role_id,
                date=cursor,
                start_time=tmpl.start_time,
                end_time=end_time,
                location_name=tmpl.location_name,
                slots_needed=tmpl.slots_needed,
                opportunity_type=tmpl.opportunity_type,
                status=OpportunityStatus.OPEN,
                min_tier=tmpl.min_tier,
                qr_checkin_enabled=tmpl.qr_checkin_enabled,
                cancellation_deadline_hours=tmpl.cancellation_deadline_hours,
                metadata_json={"source_template_id": str(tmpl.id)},
            )
            db.add(opp)
            created += 1
        cursor += timedelta(days=7)

    tmpl.last_materialised_through = body.through_date
    await db.commit()
    return MaterialiseTemplateResponse(
        success=True,
        created_count=created,
        last_materialised_through=body.through_date,
    )
