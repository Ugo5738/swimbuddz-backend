"""add session discriminator check constraint

Revision ID: c490b3168c3e
Revises: cca485ecbeb5
Create Date: 2026-05-17 04:24:36.923075

Hand-written migration — Alembic autogenerate does not detect CheckConstraint
changes; the stub was created via `alembic revision -m ...` (no
--autogenerate) so the revision ID is still Alembic-assigned and the chain
stays intact. See A1 Phase 2 in the May 2026 code review work.

This is the DB-level half of the Session ``session_type`` ↔ context-FK
discriminator rule. The Python-side enforcement (Pydantic + SQLAlchemy
event listener) shipped in commit 214e1c8 (A1 Phase 1).

The constraint is added with ``NOT VALID`` so existing rows are not
re-checked at migration time — only new INSERTs and UPDATEs are enforced.
A separate audit + ``VALIDATE CONSTRAINT`` step can run later when we've
confirmed the production data is clean (see A1 Phase 2.b in the design
note). The expression mirrors
``services.sessions_service.models._validators.validate_session_discriminator``
exactly.
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c490b3168c3e"
down_revision = "cca485ecbeb5"
branch_labels = None
depends_on = None


_CHECK_EXPR = (
    "(session_type = 'cohort_class' AND cohort_id IS NOT NULL "
    "AND event_id IS NULL AND booking_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'event' AND event_id IS NOT NULL "
    "AND cohort_id IS NULL AND booking_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'one_on_one' AND booking_id IS NOT NULL "
    "AND cohort_id IS NULL AND event_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'group_booking' AND booking_id IS NOT NULL "
    "AND cohort_id IS NULL AND event_id IS NULL AND pod_id IS NULL) "
    "OR (session_type = 'club' "
    "AND cohort_id IS NULL AND event_id IS NULL AND booking_id IS NULL) "
    "OR (session_type = 'community' "
    "AND cohort_id IS NULL AND event_id IS NULL "
    "AND booking_id IS NULL AND pod_id IS NULL)"
)


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE sessions "
        f"ADD CONSTRAINT ck_sessions_discriminator "
        f"CHECK ({_CHECK_EXPR}) "
        f"NOT VALID"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE sessions DROP CONSTRAINT IF EXISTS ck_sessions_discriminator"
    )
