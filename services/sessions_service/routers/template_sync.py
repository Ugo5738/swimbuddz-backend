"""Reviewable repair of generated operational defaults; never rewrites sales."""

import hashlib
import json
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.db.session import get_async_db
from services.sessions_service.models import Session, SessionStatus, SessionTemplate
from services.sessions_service.services.template_operations import (
    materialise_template_operations,
    template_admission,
    template_location,
    template_volunteer_slots,
)

router = APIRouter(dependencies=[Depends(require_admin)])
logger = get_logger(__name__)


class TemplateSyncRequest(BaseModel):
    from_date: date
    to_date: date
    preview_token: str | None = None

    @model_validator(mode="after")
    def valid_range(self) -> "TemplateSyncRequest":
        if not 0 <= (self.to_date - self.from_date).days <= 366:
            raise ValueError("Select a date range of at most one year")
        return self


@router.post("/{template_id}/sync-operations")
async def sync_template_operations(
    template_id: uuid.UUID,
    body: TemplateSyncRequest,
    db: AsyncSession = Depends(get_async_db),
):
    template = await db.get(SessionTemplate, template_id)
    if not template:
        raise HTTPException(404, "Template not found")
    tz = ZoneInfo("Africa/Lagos")
    rows = list(
        (
            await db.execute(
                select(Session)
                .where(
                    Session.template_id == template_id,
                    Session.starts_at > utc_now(),
                    Session.starts_at
                    >= datetime.combine(body.from_date, time.min, tzinfo=tz),
                    Session.starts_at
                    < datetime.combine(
                        body.to_date + timedelta(days=1), time.min, tzinfo=tz
                    ),
                    Session.status.in_([SessionStatus.DRAFT, SessionStatus.SCHEDULED]),
                )
                .order_by(Session.id)
                .with_for_update()
            )
        ).scalars()
    )
    location = await template_location(template)
    volunteer_slots = await template_volunteer_slots(template.id)
    changes = []
    for row in rows:
        if row.pool_id != template.pool_id or row.club_id != template.club_id:
            raise HTTPException(
                409, "A session has a venue/Club override; repair it individually"
            )
        values = {**location, **template_admission(template, row.starts_at)}
        changes.append(
            {
                "session_id": str(row.id),
                "title": row.title,
                "before": {key: getattr(row, key) for key in values},
                "after": values,
            }
        )
    token = hashlib.sha256(
        json.dumps(
            {
                "changes": changes,
                "ride_share_config": template.ride_share_config,
                "volunteer_slots": volunteer_slots,
            },
            sort_keys=True,
            default=str,
        ).encode()
    ).hexdigest()
    if body.preview_token is None:
        return {
            "preview_token": token,
            "sessions": changes,
            "applied": False,
            "volunteer_slots": volunteer_slots,
            "ride_share_config": template.ride_share_config,
        }
    if body.preview_token != token:
        raise HTTPException(
            409, "Template or sessions changed; preview the repair again"
        )
    for row, change in zip(rows, changes, strict=True):
        for key, value in change["after"].items():
            setattr(row, key, value)
    await db.commit()
    warnings = []
    for row in rows:
        try:
            await materialise_template_operations(template, row)
        except Exception:
            logger.exception("Template operations repair failed for session %s", row.id)
            warnings.append(f"Retry operational sync for session {row.id}")
    return {"applied": True, "sessions_updated": len(rows), "warnings": warnings}
