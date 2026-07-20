"""Weekly digest configuration, reporting, and tracked links."""

from __future__ import annotations

import uuid
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.media_utils import resolve_media_url
from libs.common.logging import get_logger
from libs.common.service_client import internal_get
from libs.db.session import get_async_db
from services.communications_service.models import (
    WeeklyDigestConfig,
    WeeklyDigestDispatch,
)
from services.communications_service.schemas import (
    WeeklyDigestConfigResponse,
    WeeklyDigestConfigUpdate,
    WeeklyDigestStatsResponse,
)

router = APIRouter(prefix="/digest", tags=["weekly-digest"])
AUDIENCES = {"community", "club", "academy"}
logger = get_logger(__name__)


async def _config_response(config: WeeklyDigestConfig) -> WeeklyDigestConfigResponse:
    data = config.__dict__.copy()
    data["featured_image_url"] = await resolve_media_url(config.featured_image_media_id)
    return WeeklyDigestConfigResponse.model_validate(data)


@router.get("/admin/configs", response_model=list[WeeklyDigestConfigResponse])
async def list_digest_configs(
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    configs = (
        (
            await db.execute(
                select(WeeklyDigestConfig).order_by(WeeklyDigestConfig.audience)
            )
        )
        .scalars()
        .all()
    )
    return [await _config_response(config) for config in configs]


@router.patch("/admin/configs/{audience}", response_model=WeeklyDigestConfigResponse)
async def update_digest_config(
    audience: str,
    payload: WeeklyDigestConfigUpdate,
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if audience not in AUDIENCES:
        raise HTTPException(status_code=404, detail="Digest audience not found")
    config = (
        await db.execute(
            select(WeeklyDigestConfig).where(WeeklyDigestConfig.audience == audience)
        )
    ).scalar_one_or_none()
    if config is None:
        raise HTTPException(status_code=404, detail="Digest audience not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        if field == "image_alt" and value is None:
            raise HTTPException(
                status_code=422,
                detail="image_alt cannot be null",
            )
        setattr(config, field, value)
    try:
        config.updated_by = uuid.UUID(current_user.user_id)
    except (TypeError, ValueError):
        config.updated_by = None
    await db.commit()
    await db.refresh(config)
    return await _config_response(config)


@router.get("/admin/stats", response_model=WeeklyDigestStatsResponse)
async def get_digest_stats(
    campaign_key: str | None = None,
    _: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if campaign_key is None:
        campaign_key = (
            await db.execute(
                select(WeeklyDigestDispatch.campaign_key)
                .order_by(WeeklyDigestDispatch.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    if campaign_key is None:
        return WeeklyDigestStatsResponse()

    rows = (
        await db.execute(
            select(
                WeeklyDigestDispatch.delivery_status,
                func.count(WeeklyDigestDispatch.id),
            )
            .where(WeeklyDigestDispatch.campaign_key == campaign_key)
            .group_by(WeeklyDigestDispatch.delivery_status)
        )
    ).all()
    counts = {status: int(count) for status, count in rows}
    click_row = (
        await db.execute(
            select(
                func.count(WeeklyDigestDispatch.id).filter(
                    WeeklyDigestDispatch.click_count > 0
                ),
                func.coalesce(func.sum(WeeklyDigestDispatch.click_count), 0),
            ).where(WeeklyDigestDispatch.campaign_key == campaign_key)
        )
    ).one()
    booking_stats = {"total": 0, "confirmed": 0}
    try:
        response = await internal_get(
            service_url=get_settings().SESSIONS_SERVICE_URL,
            path="/internal/sessions/bookings/campaign-stats",
            calling_service="communications",
            params={"campaign_key": campaign_key},
        )
        if response.status_code == 200:
            booking_stats = response.json()
        else:
            logger.warning(
                "Digest booking stats returned %s for %s",
                response.status_code,
                campaign_key,
            )
    except Exception as exc:  # noqa: BLE001 - reporting degrades independently
        logger.warning("Digest booking stats unavailable for %s: %s", campaign_key, exc)
    return WeeklyDigestStatsResponse(
        campaign_key=campaign_key,
        total=sum(counts.values()),
        sent=counts.get("sent", 0),
        failed=counts.get("failed", 0),
        pending=counts.get("pending", 0),
        uncertain=counts.get("sending", 0) + counts.get("unknown", 0),
        recipients_clicked=int(click_row[0] or 0),
        total_clicks=int(click_row[1] or 0),
        bookings_started=int(booking_stats.get("total") or 0),
        bookings_confirmed=int(booking_stats.get("confirmed") or 0),
    )


@router.get("/click/{token}/{kind}/{resource_id}")
async def track_digest_click(
    token: uuid.UUID,
    kind: str,
    resource_id: str,
    db: AsyncSession = Depends(get_async_db),
):
    dispatch = (
        await db.execute(
            select(WeeklyDigestDispatch).where(
                WeeklyDigestDispatch.tracking_token == token
            )
        )
    ).scalar_one_or_none()
    if dispatch is None:
        raise HTTPException(status_code=404, detail="Digest link not found")

    frontend = get_settings().FRONTEND_URL.rstrip("/")
    campaign_query = urlencode(
        {"source": "weekly_digest", "campaign": dispatch.campaign_key}
    )
    if kind in {"session", "session-manage"}:
        try:
            uuid.UUID(resource_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Session not found") from exc
        session_path = (
            f"/sessions/{quote(resource_id)}"
            if kind == "session-manage"
            else f"/sessions/{quote(resource_id)}/book"
        )
        target = f"{frontend}{session_path}?{campaign_query}"
    elif kind == "article":
        try:
            uuid.UUID(resource_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Article not found") from exc
        target = f"{frontend}/community/tips/{quote(resource_id)}?{campaign_query}"
    elif kind == "preferences" and resource_id == "me":
        target = f"{frontend}/account/settings?{campaign_query}"
    else:
        raise HTTPException(status_code=404, detail="Digest link not found")

    now = utc_now()
    dispatch.click_count += 1
    dispatch.first_clicked_at = dispatch.first_clicked_at or now
    dispatch.last_clicked_at = now
    await db.commit()
    return RedirectResponse(target, status_code=302)
