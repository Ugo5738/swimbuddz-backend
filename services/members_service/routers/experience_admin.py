"""Package configuration and operational attendance; all routes are Admin-only."""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.service_client import internal_post
from libs.db.session import get_async_db
from services.members_service.models import (
    ClubPlanSession,
    ClubPlanVersion,
    CommunityExperienceOffering,
    CommunityExperiencePurchase,
)
from services.members_service.models.experience import (
    CommunityExperienceAttendance,
    CommunityExperienceEvent,
    CommunityExperienceOrder,
    CommunityExperienceParticipant,
    ExperienceConfigurationOperation,
)
from services.members_service.schemas.club import (
    CommunityExperienceOfferingCreate,
    CommunityExperienceOfferingResponse,
)
from services.members_service.schemas.experience import (
    ExperienceAttendanceInput,
    ExperienceEventsUpdate,
)
from services.members_service.services.experience_events import (
    event_request,
    live_events,
)
from services.members_service.services.club_plan_schedule import fetch_schedule

router = APIRouter(
    prefix="/clubs/community-experiences/admin",
    tags=["experience-admin"],
    dependencies=[Depends(require_admin)],
)


async def lock_offering(db, offering_id, *, allow_configuration=False):
    offering = (
        await db.execute(
            select(CommunityExperienceOffering)
            .where(CommunityExperienceOffering.id == offering_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if offering is None:
        raise HTTPException(404, "Community Experience not found")
    if not allow_configuration:
        pending = (
            await db.execute(
                select(ExperienceConfigurationOperation.id).where(
                    ExperienceConfigurationOperation.offering_id == offering_id,
                    ExperienceConfigurationOperation.status.in_(
                        ["pending", "needs_reconciliation"]
                    ),
                )
            )
        ).first()
        if pending:
            raise HTTPException(
                409,
                "Experience configuration is pending recovery; Admin must reconcile it before changes or checkout",
            )
    return offering


async def assert_unsold(db, offering):
    orders = (
        await db.execute(
            select(CommunityExperienceOrder.id).where(
                CommunityExperienceOrder.offering_id == offering.id
            )
        )
    ).first()
    purchases = (
        await db.execute(
            select(CommunityExperiencePurchase.id).where(
                CommunityExperiencePurchase.offering_id == offering.id
            )
        )
    ).first()
    if orders or purchases:
        raise HTTPException(
            409,
            "This package already has checkout/purchase records; create a new offering version rather than changing its contents",
        )


@router.get("", response_model=list[CommunityExperienceOfferingResponse])
async def admin_offerings(db: AsyncSession = Depends(get_async_db)):
    return list(
        (
            await db.execute(
                select(CommunityExperienceOffering).order_by(
                    CommunityExperienceOffering.period_start.desc()
                )
            )
        ).scalars()
    )


@router.put("/{offering_id}", response_model=CommunityExperienceOfferingResponse)
async def update_offering(
    offering_id: uuid.UUID,
    body: CommunityExperienceOfferingCreate,
    db: AsyncSession = Depends(get_async_db),
):
    offering = await lock_offering(db, offering_id)
    if body.capacity is not None:
        from services.members_service.services.experience_ticketing import (
            occupied_places,
        )

        if body.capacity < await occupied_places(db, offering.id):
            raise HTTPException(
                409,
                "Capacity cannot be reduced below confirmed participants and active checkout holds",
            )
    # Prices may be edited for new orders. Dates/currency cannot rewrite an
    # already sold commercial period; participant prices are frozen on orders.
    if (body.period_start, body.period_end, body.currency) != (
        offering.period_start,
        offering.period_end,
        offering.currency,
    ):
        await assert_unsold(db, offering)
    for key, value in body.model_dump().items():
        setattr(offering, key, value)
    await db.commit()
    return offering


@router.get("/{offering_id}/events")
async def admin_events(
    offering_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)
):
    offering = await db.get(CommunityExperienceOffering, offering_id)
    if offering is None:
        raise HTTPException(404, "Community Experience not found")
    rows = await live_events(offering)
    links = {str(link.event_id): link for link in offering.event_links}
    return {
        "events": [
            {
                **row,
                "club_impact": links[row["id"]].club_impact,
                "replaced_session_ids": links[row["id"]].replaced_session_ids,
            }
            for row in rows
        ],
        "recommended_standard_price_kobo": sum(
            row["recommended_price_kobo"] for row in rows
        ),
    }


@router.put("/{offering_id}/events")
async def set_events(
    offering_id: uuid.UUID,
    body: ExperienceEventsUpdate,
    db: AsyncSession = Depends(get_async_db),
):
    offering = await lock_offering(db, offering_id)
    await assert_unsold(db, offering)
    ids = [str(item.event_id) for item in body.events]
    events = await event_request("query", {"event_ids": ids}) if ids else []
    if len(events) != len(ids):
        raise HTTPException(422, "One or more Events do not exist")
    by_id = {row["id"]: row for row in events}
    replacements = []
    for item in body.events:
        event = by_id[str(item.event_id)]
        tz = ZoneInfo(event.get("timezone") or "Africa/Lagos")
        start = datetime.fromisoformat(event["start_time"]).astimezone(tz).date()
        end = (
            datetime.fromisoformat(event["end_time"] or event["start_time"])
            .astimezone(tz)
            .date()
        )
        if not offering.period_start <= start <= end <= offering.period_end:
            raise HTTPException(
                422,
                "Linked Events must fall inside the Experience's commercial quarter",
            )
        if item.replaced_session_ids:
            rows = await fetch_schedule(
                session_ids=[str(value) for value in item.replaced_session_ids]
            )
            if len(rows) != len(set(item.replaced_session_ids)) or any(
                not start
                <= datetime.fromisoformat(row["starts_at"]).astimezone(tz).date()
                <= end
                for row in rows
            ):
                raise HTTPException(
                    422, "Select affected Club sessions on the Event's date(s)"
                )
            affected_plans = (
                (
                    await db.execute(
                        select(ClubPlanVersion)
                        .where(
                            ClubPlanVersion.id.in_(
                                select(ClubPlanSession.plan_version_id).where(
                                    ClubPlanSession.session_id.in_(
                                        item.replaced_session_ids
                                    )
                                )
                            )
                        )
                        .order_by(ClubPlanVersion.id)
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            if any(plan.published_at for plan in affected_plans):
                raise HTTPException(
                    409,
                    "A published Club quarter includes these sessions; resolve member coverage before replacing them",
                )
            replacements.extend(str(value) for value in item.replaced_session_ids)
    operation = ExperienceConfigurationOperation(
        offering_id=offering.id,
        status="pending",
        old_event_ids=[str(link.event_id) for link in offering.event_links],
        request=body.model_dump(mode="json"),
    )
    db.add(operation)
    await db.flush()
    operation_id = operation.id
    replacement_payload = {
        "operation_id": str(operation_id),
        "session_ids": sorted(set(replacements)),
        "reason": f"Replaced by Community Experience {offering.name}",
    }
    if replacements:
        await schedule_change_request(
            "replace", {**replacement_payload, "validate_only": True}
        )
    # Durable intent before any external write. All callers lock the offering
    # and reject pending configuration, including Club-bundle reservations.
    await db.commit()
    offering = await lock_offering(db, offering_id, allow_configuration=True)
    operation = await db.get(
        ExperienceConfigurationOperation, operation_id, populate_existing=True
    )
    if operation.status != "pending":
        raise HTTPException(
            409, "This configuration was recovered; submit a new attempt"
        )
    try:
        await event_request(
            "bind",
            {
                "offering_id": str(offering_id),
                "event_ids": ids,
                "operation_id": str(operation_id),
            },
        )
        if replacements:
            await schedule_change_request("replace", replacement_payload)
        offering.event_links.clear()
        await db.flush()
        offering.event_links = [
            CommunityExperienceEvent(
                event_id=item.event_id,
                sort_order=index,
                club_impact=item.club_impact,
                replaced_session_ids=[
                    str(value) for value in item.replaced_session_ids
                ],
                event_snapshot=by_id[str(item.event_id)],
            )
            for index, item in enumerate(body.events)
        ]
        operation.status = "applied"
        await db.commit()
    except Exception as exc:
        await db.rollback()
        # An uncertain commit must be read back before compensating. If the DB
        # is unavailable, durable pending intent remains for Admin recovery.
        await lock_offering(db, offering_id, allow_configuration=True)
        operation = await db.get(
            ExperienceConfigurationOperation, operation_id, populate_existing=True
        )
        if operation.status != "applied":
            await compensate_configuration(db, operation)
            raise HTTPException(
                409,
                f"Configuration did not complete. Operation {operation_id}: {operation.status}. Review recovery before retrying.",
            ) from exc
    return await admin_events(offering_id, db)


async def schedule_change_request(action, payload):
    response = await internal_post(
        service_url=get_settings().SESSIONS_SERVICE_URL,
        path=f"/internal/sessions/club-schedule/{action}",
        calling_service="members",
        json=payload,
    )
    if response.status_code >= 400:
        raise HTTPException(
            response.status_code,
            response.json().get("detail", "Schedule operation failed"),
        )
    return response.json()


async def compensate_configuration(db, operation):
    failures = []
    if any(item.get("replaced_session_ids") for item in operation.request["events"]):
        try:
            await schedule_change_request(f"operations/{operation.id}/undo", {})
        except Exception:
            failures.append("Session restoration")
    try:
        await event_request(
            "bind",
            {
                "offering_id": str(operation.offering_id),
                "event_ids": operation.old_event_ids,
                "operation_id": str(operation.id),
                "compensate": True,
            },
        )
    except Exception:
        failures.append("Event binding restoration")
    operation.status = "needs_reconciliation" if failures else "failed"
    operation.error = (
        ", ".join(failures) + " requires retry"
        if failures
        else "Previous configuration restored; a new attempt is safe"
    )
    await db.commit()


@router.get("/{offering_id}/operations")
async def configuration_operations(
    offering_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)
):
    rows = (
        await db.execute(
            select(ExperienceConfigurationOperation)
            .where(
                ExperienceConfigurationOperation.offering_id == offering_id,
            )
            .order_by(ExperienceConfigurationOperation.created_at.desc())
            .limit(20)
        )
    ).scalars()
    return [
        {
            "id": row.id,
            "status": row.status,
            "error": row.error,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/{offering_id}/operations/{operation_id}/recover")
async def recover_configuration(
    offering_id: uuid.UUID,
    operation_id: uuid.UUID,
    db: AsyncSession = Depends(get_async_db),
):
    await lock_offering(db, offering_id, allow_configuration=True)
    operation = await db.get(ExperienceConfigurationOperation, operation_id)
    if not operation or operation.offering_id != offering_id:
        raise HTTPException(404, "Configuration operation not found")
    if operation.status in {"pending", "needs_reconciliation"}:
        await compensate_configuration(db, operation)
    return {"id": operation.id, "status": operation.status, "error": operation.error}


@router.get("/{offering_id}/participants")
async def participants(
    offering_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)
):
    rows = (
        await db.execute(
            select(CommunityExperienceParticipant, CommunityExperienceOrder)
            .join(CommunityExperienceOrder)
            .where(
                CommunityExperienceOrder.offering_id == offering_id,
                CommunityExperienceOrder.status == "confirmed",
            )
        )
    ).all()
    attendance = (
        (
            await db.execute(
                select(CommunityExperienceAttendance).where(
                    CommunityExperienceAttendance.participant_id.in_(
                        [person.id for person, _ in rows]
                    )
                )
            )
        )
        .scalars()
        .all()
        if rows
        else []
    )
    return [
        {
            "id": participant.id,
            "order_id": order.id,
            "name": participant.full_name,
            "email": participant.email,
            "phone": participant.phone,
            "emergency_contact": participant.emergency_contact,
            "ticket_kind": participant.ticket_kind,
            "price_kobo": participant.price_kobo,
            "waiver_accepted_at": participant.waiver_accepted_at,
            "payment_reference": order.payment_reference,
            "checked_in_event_ids": [
                str(item.event_id)
                for item in attendance
                if item.participant_id == participant.id
            ],
        }
        for participant, order in rows
    ]


@router.get("/{offering_id}/club-sessions")
async def affected_session_options(
    offering_id: uuid.UUID, pool_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)
):
    offering = await db.get(CommunityExperienceOffering, offering_id)
    if not offering:
        raise HTTPException(404, "Experience not found")
    return await fetch_schedule(
        pool_id=str(pool_id),
        period_start=offering.period_start.isoformat(),
        period_end=offering.period_end.isoformat(),
    )


@router.post("/participants/{participant_id}/check-in")
async def check_in(
    participant_id: uuid.UUID,
    body: ExperienceAttendanceInput,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    from sqlalchemy.dialects.postgresql import insert

    participant = await db.get(CommunityExperienceParticipant, participant_id)
    order = (
        await db.get(CommunityExperienceOrder, participant.order_id)
        if participant
        else None
    )
    if not order or order.status != "confirmed":
        raise HTTPException(409, "Only a confirmed participant can attend")
    if not participant.waiver_accepted_at or not participant.emergency_contact.get(
        "phone"
    ):
        raise HTTPException(
            409,
            "Participant must complete their safety details and waiver before check-in",
        )
    link = (
        await db.execute(
            select(CommunityExperienceEvent.id).where(
                CommunityExperienceEvent.offering_id == order.offering_id,
                CommunityExperienceEvent.event_id == body.event_id,
            )
        )
    ).first()
    if not link:
        raise HTTPException(
            422, "This Event is not included in the participant's Experience"
        )
    await db.execute(
        insert(CommunityExperienceAttendance)
        .values(
            participant_id=participant_id,
            event_id=body.event_id,
            checked_in_by=admin.user_id,
        )
        .on_conflict_do_nothing(index_elements=["participant_id", "event_id"])
    )
    await db.commit()
    return {"checked_in": True}
