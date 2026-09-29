"""add_club_session_capacity_holds

Hand-written migration — explicit additive table and indexes; includes a
status CHECK constraint and a guarded downgrade preserving committed seats.

Revision ID: e30550028159
Revises: 82f070b47973
Create Date: 2026-09-28 17:37:40.913574
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "e30550028159"
down_revision = "82f070b47973"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from sqlalchemy.dialects import postgresql

    op.create_table(
        "club_session_holds",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("club_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_reference", sa.String(128), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "payment_reference",
            "session_id",
            name="uq_club_session_hold_reference_session",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'protected', 'consumed', 'released')",
            name="ck_club_session_hold_status",
        ),
    )
    op.create_index(
        "ix_club_session_holds_capacity",
        "club_session_holds",
        ["session_id", "status", "expires_at"],
    )
    op.create_index(
        "ix_club_session_holds_payment", "club_session_holds", ["payment_reference"]
    )


def downgrade() -> None:
    if (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM club_session_holds WHERE status = 'protected' OR (status = 'active' AND expires_at > now()))"
            )
        )
        .scalar()
    ):
        raise RuntimeError(
            "Reconcile live Club swim holds before downgrading; dropping them would sell committed seats twice"
        )
    op.drop_table("club_session_holds")
