"""add Club checkout seat reservations

Revision ID: b6d4e8f2a731
Revises: a1c7d9e2f401
Create Date: 2026-09-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b6d4e8f2a731"
down_revision: Union[str, None] = "a1c7d9e2f401"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "club_plan_versions",
        sa.Column("pool_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "club_plan_versions",
        sa.Column("operating_area_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE club_plan_versions AS plan
        SET pool_id = club.default_pool_id,
            operating_area_id = club.operating_area_id
        FROM clubs AS club
        WHERE club.id = plan.club_id
        """
    )
    op.create_index(
        op.f("ix_club_plan_versions_pool_id"),
        "club_plan_versions",
        ["pool_id"],
    )
    op.create_index(
        op.f("ix_club_plan_versions_operating_area_id"),
        "club_plan_versions",
        ["operating_area_id"],
    )
    op.create_table(
        "club_enrollment_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_reference", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'consumed', 'released')",
            name="ck_club_enrollment_reservation_status",
        ),
        sa.ForeignKeyConstraint(
            ["application_id"], ["club_applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id"], ["club_plan_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id",
            "plan_version_id",
            name="uq_club_enrollment_reservation_application_plan",
        ),
    )
    op.create_index(
        op.f("ix_club_enrollment_reservations_application_id"),
        "club_enrollment_reservations",
        ["application_id"],
    )
    op.create_index(
        op.f("ix_club_enrollment_reservations_plan_version_id"),
        "club_enrollment_reservations",
        ["plan_version_id"],
    )
    op.create_index(
        op.f("ix_club_enrollment_reservations_payment_reference"),
        "club_enrollment_reservations",
        ["payment_reference"],
    )
    op.create_index(
        op.f("ix_club_enrollment_reservations_expires_at"),
        "club_enrollment_reservations",
        ["expires_at"],
    )
    op.create_index(
        "ix_club_enrollment_reservations_live",
        "club_enrollment_reservations",
        ["plan_version_id", "status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_club_enrollment_reservations_live",
        table_name="club_enrollment_reservations",
    )
    op.drop_index(
        op.f("ix_club_enrollment_reservations_expires_at"),
        table_name="club_enrollment_reservations",
    )
    op.drop_index(
        op.f("ix_club_enrollment_reservations_payment_reference"),
        table_name="club_enrollment_reservations",
    )
    op.drop_index(
        op.f("ix_club_enrollment_reservations_plan_version_id"),
        table_name="club_enrollment_reservations",
    )
    op.drop_index(
        op.f("ix_club_enrollment_reservations_application_id"),
        table_name="club_enrollment_reservations",
    )
    op.drop_table("club_enrollment_reservations")
    op.drop_index(
        op.f("ix_club_plan_versions_operating_area_id"),
        table_name="club_plan_versions",
    )
    op.drop_index(
        op.f("ix_club_plan_versions_pool_id"),
        table_name="club_plan_versions",
    )
    op.drop_column("club_plan_versions", "operating_area_id")
    op.drop_column("club_plan_versions", "pool_id")
