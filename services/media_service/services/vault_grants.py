"""Access-window and notification rules shared by media-vault entry points."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from html import escape
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.service_client import (
    dispatch_notification,
    get_media_vault_assignments,
)
from services.media_service.models import MediaVault, MediaVaultGrant

CONTRIBUTOR_REOPEN_DAYS = 7


def ensure_contributor_window(
    vault: MediaVault,
    *,
    starts_at: datetime,
    expires_at: datetime,
    now: Optional[datetime] = None,
) -> tuple[datetime, datetime]:
    """Return a usable grant window and keep the vault upload window aligned.

    An admin may add an uploader to a past session.  In that case a client can
    legitimately submit the old session window, but persisting it would create
    an already-expired grant.  Reopen both the grant and vault for seven days so
    visibility and upload authorization cannot disagree.
    """

    current = now or utc_now()
    normalized_starts = min(starts_at, current)
    normalized_expires = expires_at
    if normalized_expires <= current:
        normalized_expires = current + timedelta(days=CONTRIBUTOR_REOPEN_DAYS)

    vault.upload_opens_at = min(vault.upload_opens_at, normalized_starts)
    vault.upload_closes_at = max(vault.upload_closes_at, normalized_expires)
    if (
        vault.status != "archived"
        and vault.upload_opens_at <= current <= vault.upload_closes_at
    ):
        vault.status = "open"
    return normalized_starts, normalized_expires


async def notify_vault_access(
    *,
    vault: MediaVault,
    member_id: uuid.UUID,
    role: str,
    expires_at: datetime,
) -> Optional[dict]:
    """Send a durable in-app assignment alert and a preference-aware email."""

    role_label = "media uploader" if role == "contributor" else "social curator"
    action_path = f"/account/media-vault/{vault.id}"
    action_url = f"{get_settings().FRONTEND_URL.rstrip('/')}{action_path}"
    expiry_label = expires_at.strftime("%A, %d %B %Y at %H:%M UTC")
    if role == "contributor":
        responsibility = "upload full-quality session photos and videos"
    else:
        responsibility = "review, shortlist, and download the session media"
    body = (
        f"You have been assigned as the {role_label} for {vault.title}. "
        f"You can {responsibility}. Access ends {expiry_label}."
    )
    html_body = (
        f"<p>You have been assigned as the <strong>{escape(role_label)}</strong> for "
        f"<strong>{escape(vault.title)}</strong>.</p>"
        f"<p>You can {escape(responsibility)}. Access ends {escape(expiry_label)}.</p>"
        f'<p><a href="{escape(action_url, quote=True)}">Open the media vault</a></p>'
    )
    return await dispatch_notification(
        type="media_vault_access_granted",
        category="media",
        member_ids=[str(member_id)],
        title=f"Media vault assignment: {vault.title}",
        body=body,
        action_url=action_path,
        icon="camera",
        metadata={"vault_id": str(vault.id), "role": role},
        channels=["in_app", "email"],
        email_template="media_vault_access_granted",
        email_data={"body": body, "html_content": html_body},
        calling_service="media",
        # Assignment notifications are history.  The access itself still
        # expires, but the alert should not disappear at the same instant.
        expires_at=None,
    )


async def sync_volunteer_grants(
    db: AsyncSession,
    *,
    vault: MediaVault,
    created_by: Optional[uuid.UUID],
) -> list[MediaVaultGrant]:
    """Idempotently mirror media/gallery volunteer claims into vault grants."""

    assignments = await get_media_vault_assignments(
        calling_service="media",
        session_id=str(vault.session_id) if vault.session_id else None,
        event_id=str(vault.event_id) if vault.event_id else None,
    )
    grants: list[MediaVaultGrant] = []
    notifications: list[tuple[uuid.UUID, str, datetime]] = []
    active_keys: set[tuple[uuid.UUID, str]] = set()
    current = utc_now()
    for assignment in assignments:
        member_id = uuid.UUID(str(assignment["member_id"]))
        role = str(assignment["role"])
        active_keys.add((member_id, role))
        grant = await db.scalar(
            select(MediaVaultGrant).where(
                MediaVaultGrant.vault_id == vault.id,
                MediaVaultGrant.member_id == member_id,
                MediaVaultGrant.role == role,
            )
        )
        starts_at = vault.upload_opens_at
        expires_at = (
            vault.upload_closes_at
            if role == "contributor"
            else vault.upload_closes_at + timedelta(days=30)
        )
        if role == "contributor":
            starts_at, expires_at = ensure_contributor_window(
                vault,
                starts_at=starts_at,
                expires_at=expires_at,
                now=current,
            )
        should_notify = (
            grant is None or grant.revoked_at is not None or grant.expires_at <= current
        )
        if grant:
            grant.starts_at = starts_at
            grant.expires_at = expires_at
            grant.source = "volunteer_assignment"
            grant.source_reference_id = str(assignment["slot_id"])
            grant.can_download_originals = role == "curator"
            grant.revoked_at = None
        else:
            grant = MediaVaultGrant(
                vault_id=vault.id,
                member_id=member_id,
                role=role,
                starts_at=starts_at,
                expires_at=expires_at,
                source="volunteer_assignment",
                source_reference_id=str(assignment["slot_id"]),
                can_download_originals=role == "curator",
                created_by=created_by,
            )
            db.add(grant)
        if should_notify:
            notifications.append((member_id, role, expires_at))
        grants.append(grant)

    existing_rows = await db.execute(
        select(MediaVaultGrant).where(
            MediaVaultGrant.vault_id == vault.id,
            MediaVaultGrant.source == "volunteer_assignment",
            MediaVaultGrant.revoked_at.is_(None),
        )
    )
    for grant in existing_rows.scalars().all():
        if (grant.member_id, grant.role) not in active_keys:
            grant.revoked_at = current
    await db.commit()

    for member_id, role, expires_at in notifications:
        await notify_vault_access(
            vault=vault,
            member_id=member_id,
            role=role,
            expires_at=expires_at,
        )
    return grants
