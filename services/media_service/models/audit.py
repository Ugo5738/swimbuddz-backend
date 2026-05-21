"""Audit log for admin-side media access.

Every admin action that surfaces or extracts a private-bucket asset
(listing an enrollment's evidence, fetching a single item's URL,
issuing a download presigned-URL) writes a row here. The table is
append-only — rows are never updated and the only delete path is a
future retention/pseudonymisation job (see
``docs/design/ACADEMY_ADMIN_CONTROLS_DESIGN.md`` §9.4).

The shape follows the B4 canonical audit-log shape
(``docs/design/B4_AUDIT_LOG_UNIFICATION.md``) so this table can adopt
the shared mixin without a data migration once B4 lands.

**Service isolation note.** ``actor_id`` and ``entity_id`` are plain
UUID columns, not foreign keys, because they reference rows owned by
other services (auth users; media items). The architecture forbids
cross-service FK constraints — and keeping them unconstrained is also
what allows the future pseudonymisation pass to rewrite actor_id
values without breaking referential integrity.
"""

import uuid
from datetime import datetime
from typing import Optional

from libs.common.datetime_utils import utc_now
from libs.db.base import Base
from sqlalchemy import DateTime, String, Text
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column


class MediaAuditLog(Base):
    """Append-only audit row for admin-side media access.

    Schema follows the B4 canonical audit-log shape so we can adopt
    the unified mixin without a data migration later.
    """

    __tablename__ = "media_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Canonical B4 fields ──────────────────────────────────────────────
    # ``domain`` is constant for this table ("media") — kept on the row
    # so a future merged-view query can union audit tables across
    # services without losing the source domain.
    domain: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # ``entity_type`` is constant for this table ("media_item"). Kept
    # for B4-shape symmetry; future media-domain audit rows for other
    # entity kinds (albums, etc.) would set this differently.
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # The media_items.id this row is about. Not a FK — see module
    # docstring.
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Action namespace: ``media.admin.list`` | ``media.admin.view`` |
    # ``media.admin.download``. Namespaced so future non-admin or
    # cross-domain actions can be added without ambiguity.
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    # Auth user UUID (admin) — or a synthetic principal UUID from
    # ``libs.common.principals`` for automated paths. Not a FK; see
    # module docstring.
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # Denormalised label (email / name) of the actor at the time of
    # the event. Stored so the audit row stays readable even after
    # the underlying user record is renamed, deleted, or pseudonymised.
    actor_label: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # For state-changing events (none on this table today, but the B4
    # shape demands the column). NULL for media views/downloads since
    # nothing on the item itself changes.
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Optional free-text justification — e.g. "responding to dispute
    # ticket #1234". NULL for routine surfacing.
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # IP the admin's request came from. INET so it's queryable / can
    # be range-filtered. Nullable because some internal paths may not
    # have a client IP (e.g. cron-driven audits).
    ip_address: Mapped[Optional[str]] = mapped_column(INET, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<MediaAuditLog {self.action} entity={self.entity_id} "
            f"actor={self.actor_id} at={self.created_at.isoformat()}>"
        )
