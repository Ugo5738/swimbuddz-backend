"""Bulk member-record loading + name formatting.

Single in-service DB query helpers used by response hydrators and
notification builders. `_short_display_name` enforces the public
"First L." privacy convention.
"""

import uuid
from typing import List, Optional

from libs.common.logging import get_logger
from services.members_service.models import (
    Member,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

CHALLENGES_CALLING_SERVICE = "members_service.challenges"
logger = get_logger(__name__)


async def _load_member_records(member_ids: List[uuid.UUID], db: AsyncSession) -> dict:
    """Bulk-load member name records by id (single in-service DB query).

    Returns dict[UUID -> (first_name, last_name)]. The cross-service
    equivalent — `libs.common.service_client.get_members_bulk` —
    forwards to `/internal/members/bulk` over HTTP and is intended for
    OTHER services calling members_service. Inside members_service we
    query the local Member table directly to skip the HTTP hop.

    Callers pick the formatting they need via _full_name / _short_name.
    """
    unique = list({mid for mid in member_ids if mid is not None})
    if not unique:
        return {}
    rows = await db.execute(
        select(Member.id, Member.first_name, Member.last_name).where(
            Member.id.in_(unique)
        )
    )
    return {row.id: (row.first_name, row.last_name) for row in rows.all()}


def _full_name(record: Optional[tuple]) -> Optional[str]:
    """Format a (first, last) tuple as "First Last". None if record missing."""
    if not record:
        return None
    first = (record[0] or "").strip()
    last = (record[1] or "").strip()
    full = f"{first} {last}".strip()
    return full or None


async def _load_member_names(member_ids: List[uuid.UUID], db: AsyncSession) -> dict:
    """Convenience: bulk-resolve member ids → "First Last" strings.

    Thin wrapper around _load_member_records + _full_name; kept so the
    admin-facing call sites stay readable.
    """
    records = await _load_member_records(member_ids, db)
    return {mid: _full_name(rec) for mid, rec in records.items() if _full_name(rec)}


def _short_display_name(record: Optional[tuple]) -> str:
    """Render a privacy-friendly public display name: "First L." form.

    Used on the public landing page to identify a winner without leaking
    the full surname. Accepts a (first_name, last_name) tuple from
    _load_member_records.
    """
    if not record:
        return "Anonymous"
    first = (record[0] or "").strip()
    last = (record[1] or "").strip()
    if not first and not last:
        return "Anonymous"
    if not last:
        return first
    return f"{first} {last[0]}.".strip()
