"""Admin event planning: recurring templates and workbook imports."""

import uuid
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.currency import naira_to_kobo
from libs.db.session import get_async_db
from services.events_service.models import Event, EventTemplate, MemberRef
from services.events_service.schemas.planning import (
    CalendarImportCommitRequest,
    CalendarImportCommitResponse,
    CalendarImportPreviewResponse,
    EventGenerationResponse,
    EventOccurrence,
    EventOccurrenceRange,
    EventTemplateCreate,
    EventTemplateResponse,
    EventTemplateUpdate,
)
from services.events_service.services.calendar_import import parse_calendar_import
from services.events_service.services.recurrence import build_occurrences

router = APIRouter(prefix="/events/planning", tags=["event-planning"])
MAX_WORKBOOK_BYTES = 5 * 1024 * 1024


async def _admin_member(admin: AuthUser, db: AsyncSession) -> MemberRef:
    member = (
        await db.execute(select(MemberRef).where(MemberRef.auth_id == admin.user_id))
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Admin member profile not found")
    return member


def _template_dict(template: EventTemplate) -> dict:
    return {
        "id": template.id,
        "title": template.title,
        "description": template.description,
        "event_type": template.event_type,
        "audience": template.audience,
        "visibility": template.visibility,
        "location_type": template.location_type,
        "timezone": template.timezone,
        "location_area": template.location_area,
        "is_location_private": template.is_location_private,
        "location": template.location,
        "local_start_time": template.local_start_time,
        "duration_minutes": template.duration_minutes,
        "max_capacity": template.max_capacity,
        "tier_access": template.tier_access,
        "pool_id": template.pool_id,
        "cost_naira": (
            template.cost_kobo / 100.0 if template.cost_kobo is not None else None
        ),
        "frequency": template.frequency,
        "interval": template.interval,
        "day_of_week": template.day_of_week,
        "week_of_month": template.week_of_month,
        "day_of_month": template.day_of_month,
        "month_of_year": template.month_of_year,
        "starts_on": template.starts_on,
        "ends_on": template.ends_on,
        "is_active": template.is_active,
        "created_by": template.created_by,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def _template_values(payload: EventTemplateCreate) -> dict:
    values = payload.model_dump(exclude={"cost_naira"})
    values["cost_kobo"] = (
        naira_to_kobo(payload.cost_naira) if payload.cost_naira is not None else None
    )
    return values


@router.get("/templates", response_model=List[EventTemplateResponse])
async def list_event_templates(
    active_only: bool = True,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    query = select(EventTemplate)
    if active_only:
        query = query.where(EventTemplate.is_active.is_(True))
    templates = (
        (await db.execute(query.order_by(EventTemplate.title.asc()))).scalars().all()
    )
    return [
        EventTemplateResponse.model_validate(_template_dict(item)) for item in templates
    ]


@router.post(
    "/templates",
    response_model=EventTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_event_template(
    payload: EventTemplateCreate,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    member = await _admin_member(admin, db)
    template = EventTemplate(**_template_values(payload), created_by=member.id)
    db.add(template)
    await db.commit()
    await db.refresh(template)
    return EventTemplateResponse.model_validate(_template_dict(template))


@router.patch("/templates/{template_id}", response_model=EventTemplateResponse)
async def update_event_template(
    template_id: uuid.UUID,
    payload: EventTemplateUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    template = await db.get(EventTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Event template not found")
    merged = {
        key: value
        for key, value in _template_dict(template).items()
        if key in EventTemplateCreate.model_fields
    }
    merged.update(payload.model_dump(exclude_unset=True))
    validated = EventTemplateCreate.model_validate(merged)
    for field, value in _template_values(validated).items():
        setattr(template, field, value)
    await db.commit()
    await db.refresh(template)
    return EventTemplateResponse.model_validate(_template_dict(template))


@router.delete(
    "/templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_event_template(
    template_id: uuid.UUID,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    template = await db.get(EventTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Event template not found")
    await db.delete(template)
    await db.commit()


@router.post(
    "/templates/{template_id}/preview",
    response_model=list[EventOccurrence],
)
async def preview_event_template(
    template_id: uuid.UUID,
    payload: EventOccurrenceRange,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    template = await db.get(EventTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Event template not found")
    return [
        item.model_dump(mode="json")
        for item in build_occurrences(template, payload.from_date, payload.to_date)
    ]


@router.post(
    "/templates/{template_id}/generate",
    response_model=EventGenerationResponse,
)
async def generate_event_drafts(
    template_id: uuid.UUID,
    payload: EventOccurrenceRange,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    template = await db.get(EventTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="Event template not found")
    if not template.is_active:
        raise HTTPException(
            status_code=409, detail="Inactive templates cannot generate events"
        )

    occurrences = build_occurrences(template, payload.from_date, payload.to_date)
    keys = [item.external_key for item in occurrences]
    existing = (
        set(
            (
                await db.execute(
                    select(Event.external_key).where(Event.external_key.in_(keys))
                )
            )
            .scalars()
            .all()
        )
        if keys
        else set()
    )
    matching_starts = (
        set(
            (
                await db.execute(
                    select(Event.start_time).where(
                        func.lower(Event.title) == template.title.lower(),
                        Event.start_time.in_([item.start_time for item in occurrences]),
                    )
                )
            )
            .scalars()
            .all()
        )
        if occurrences
        else set()
    )
    skipped = 0
    for occurrence in occurrences:
        if (
            occurrence.external_key in existing
            or occurrence.start_time in matching_starts
        ):
            skipped += 1
            continue
        db.add(
            Event(
                title=template.title,
                description=template.description,
                event_type=template.event_type,
                audience=template.audience,
                visibility=template.visibility,
                status="draft",
                location_type=template.location_type,
                timezone=template.timezone,
                location_area=template.location_area,
                is_location_private=template.is_location_private,
                location=template.location,
                start_time=occurrence.start_time,
                end_time=occurrence.end_time,
                max_capacity=template.max_capacity,
                cost_kobo=template.cost_kobo,
                tier_access=template.tier_access,
                pool_id=template.pool_id,
                created_by=template.created_by,
                template_id=template.id,
                external_key=occurrence.external_key,
            )
        )
    await db.commit()
    return EventGenerationResponse(
        created=len(occurrences) - skipped,
        skipped_existing=skipped,
        occurrences=occurrences,
    )


@router.post(
    "/imports/xlsx/preview",
    response_model=CalendarImportPreviewResponse,
)
async def preview_calendar_workbook(
    file: UploadFile = File(...),
    _admin: AuthUser = Depends(require_admin),
):
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Upload an .xlsx workbook")
    content = await file.read(MAX_WORKBOOK_BYTES + 1)
    if len(content) > MAX_WORKBOOK_BYTES:
        raise HTTPException(status_code=413, detail="Workbook exceeds the 5 MB limit")
    try:
        return parse_calendar_import(content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/imports/commit",
    response_model=CalendarImportCommitResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_calendar_drafts(
    payload: CalendarImportCommitRequest,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    member = await _admin_member(admin, db)
    requested_keys = [row.external_key for row in payload.rows]
    if len(set(requested_keys)) != len(requested_keys):
        raise HTTPException(
            status_code=400, detail="Import contains duplicate External Keys"
        )
    existing = set(
        (
            await db.execute(
                select(Event.external_key).where(Event.external_key.in_(requested_keys))
            )
        )
        .scalars()
        .all()
    )
    existing_pairs = set(
        (
            await db.execute(
                select(Event.title, Event.start_time).where(
                    func.lower(Event.title).in_(
                        {row.title.lower() for row in payload.rows}
                    ),
                    Event.start_time.in_([row.start_time for row in payload.rows]),
                )
            )
        ).all()
    )
    normalized_existing_pairs = {
        (title.lower(), start_time) for title, start_time in existing_pairs
    }
    created_events: list[Event] = []
    skipped = 0
    for row in payload.rows:
        if (
            row.external_key in existing
            or (row.title.lower(), row.start_time) in normalized_existing_pairs
        ):
            skipped += 1
            continue
        values = row.model_dump(
            exclude={"cost_naira", "external_key", "source_sheet", "source_row"}
        )
        values["status"] = "draft"
        values["cost_kobo"] = (
            naira_to_kobo(row.cost_naira) if row.cost_naira is not None else None
        )
        event = Event(
            **values,
            external_key=row.external_key,
            created_by=member.id,
        )
        db.add(event)
        created_events.append(event)
    await db.flush()
    created_ids = [event.id for event in created_events]
    await db.commit()
    return CalendarImportCommitResponse(
        created=len(created_ids),
        skipped_existing=skipped,
        created_event_ids=created_ids,
    )
