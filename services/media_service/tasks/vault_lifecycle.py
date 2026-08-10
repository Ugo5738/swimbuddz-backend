"""Automatically provision private media vaults for scheduled sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client import list_scheduled_sessions
from libs.db.config import AsyncSessionLocal
from services.media_service.models import MediaVault
from services.media_service.services.vault_grants import sync_volunteer_grants
from services.media_service.services.vault_templates import (
    DEFAULT_MEDIA_VAULT_CHECKLIST,
    DEFAULT_MEDIA_VAULT_CONSENT_NOTICE,
    default_media_coverage_settings,
)

logger = get_logger(__name__)
settings = get_settings()


def vault_fields_from_session(session: dict, *, now: datetime) -> dict:
    """Translate a sessions-service payload into deterministic vault fields."""

    starts_at = datetime.fromisoformat(str(session["starts_at"]))
    ends_at = datetime.fromisoformat(str(session["ends_at"]))
    timezone_name = str(session.get("timezone") or "Africa/Lagos")
    try:
        local_timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone_name = "Africa/Lagos"
        local_timezone = ZoneInfo(timezone_name)
    upload_opens_at = starts_at - timedelta(hours=4)
    # The operating standard asks for upload within 24 hours; the extra two
    # days are a practical recovery window for weak poolside connectivity.
    upload_closes_at = ends_at + timedelta(hours=72)
    status = "scheduled"
    if upload_opens_at <= now <= upload_closes_at:
        status = "open"
    elif now > upload_closes_at:
        status = "review"
    return {
        "session_id": uuid.UUID(str(session["id"])),
        "title": str(session["title"]),
        "description": session.get("description"),
        "capture_date": starts_at.astimezone(local_timezone).date(),
        "starts_at": starts_at,
        "ends_at": ends_at,
        "timezone": timezone_name,
        "location_name": session.get("location_name") or session.get("location"),
        "status": status,
        "upload_opens_at": upload_opens_at,
        "upload_closes_at": upload_closes_at,
        "auto_transcode": False,
        "retention_days": 730,
        "consent_notice": DEFAULT_MEDIA_VAULT_CONSENT_NOTICE,
        "shot_checklist": list(DEFAULT_MEDIA_VAULT_CHECKLIST),
        "settings_json": default_media_coverage_settings(),
        "created_by": None,
    }


async def sync_session_vaults() -> dict[str, int | str]:
    """Create missing vaults idempotently for recent and upcoming sessions."""

    if not settings.MEDIA_VAULT_AUTO_CREATE_ENABLED:
        return {"status": "disabled", "created": 0, "existing": 0, "failed": 0}

    now = utc_now()
    start_date = now - timedelta(
        days=max(0, settings.MEDIA_VAULT_AUTO_CREATE_LOOKBACK_DAYS)
    )
    end_date = now + timedelta(
        days=max(1, settings.MEDIA_VAULT_AUTO_CREATE_HORIZON_DAYS)
    )
    sessions = await list_scheduled_sessions(
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        calling_service="media",
    )
    allowed_types = {
        value.strip().lower()
        for value in settings.MEDIA_VAULT_AUTO_CREATE_SESSION_TYPES.split(",")
        if value.strip()
    }
    candidates = [
        session
        for session in sessions
        if str(session.get("session_type", "")).lower() in allowed_types
    ]
    created = 0
    existing = 0
    failed = 0

    async with AsyncSessionLocal() as db:
        for session in candidates:
            session_id = uuid.UUID(str(session["id"]))
            vault = await db.scalar(
                select(MediaVault).where(MediaVault.session_id == session_id)
            )
            if vault:
                existing += 1
                try:
                    await sync_volunteer_grants(db, vault=vault, created_by=None)
                except Exception:
                    await db.rollback()
                    logger.warning(
                        "Could not refresh volunteer grants for vault %s",
                        vault.id,
                        exc_info=True,
                    )
                continue
            vault = MediaVault(**vault_fields_from_session(session, now=now))
            db.add(vault)
            try:
                await db.commit()
                await db.refresh(vault)
            except IntegrityError:
                await db.rollback()
                existing += 1
                continue
            except Exception:
                await db.rollback()
                failed += 1
                logger.exception(
                    "Failed to create media vault for session %s", session_id
                )
                continue

            created += 1
            try:
                await sync_volunteer_grants(db, vault=vault, created_by=None)
            except Exception:
                await db.rollback()
                logger.warning(
                    "Created vault %s but volunteer grant sync failed",
                    vault.id,
                    exc_info=True,
                )

    result: dict[str, int | str] = {
        "status": "ok",
        "created": created,
        "existing": existing,
        "failed": failed,
    }
    logger.info("Session media-vault sync completed: %s", result)
    return result
