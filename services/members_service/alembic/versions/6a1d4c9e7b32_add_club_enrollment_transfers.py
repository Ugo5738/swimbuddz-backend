"""add_club_enrollment_transfers

Audit table for permanent home-Club location changes. Casual cross-location
visits remain SessionBooking rows and do not mutate ClubEnrollment.

Revision ID: 6a1d4c9e7b32
Revises: 1cbbd94c744f
Create Date: 2026-10-04 09:35:00
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "6a1d4c9e7b32"
down_revision = "1cbbd94c744f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "club_enrollment_transfers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_enrollment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_enrollment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_club_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_club_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_plan_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_pod_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_payment_mode", sa.String(length=32), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            nullable=False,
            server_default="completed",
        ),
        sa.Column("requested_by_auth_id", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_enrollment_id"], ["club_enrollments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_enrollment_id"], ["club_enrollments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_club_id"], ["clubs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_club_id"], ["clubs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["target_plan_version_id"],
            ["club_plan_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["target_pod_id"], ["pods.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "source_club_id <> target_club_id",
            name="ck_club_enrollment_transfer_different_clubs",
        ),
        sa.CheckConstraint(
            "source_payment_mode IN ('quarterly_prepaid', 'transition_per_session')",
            name="ck_club_enrollment_transfer_payment_mode",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'cancelled')",
            name="ck_club_enrollment_transfer_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_club_enrollment_transfers_member_created",
        "club_enrollment_transfers",
        ["member_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_club_enrollment_transfers_source_enrollment_id",
        "club_enrollment_transfers",
        ["source_enrollment_id"],
        unique=False,
    )
    op.create_index(
        "ix_club_enrollment_transfers_target_enrollment_id",
        "club_enrollment_transfers",
        ["target_enrollment_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_club_enrollment_transfers_target_enrollment_id",
        table_name="club_enrollment_transfers",
    )
    op.drop_index(
        "ix_club_enrollment_transfers_source_enrollment_id",
        table_name="club_enrollment_transfers",
    )
    op.drop_index(
        "ix_club_enrollment_transfers_member_created",
        table_name="club_enrollment_transfers",
    )
    op.drop_table("club_enrollment_transfers")
