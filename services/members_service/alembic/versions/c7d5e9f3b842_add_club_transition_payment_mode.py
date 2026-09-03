"""add Club transition payment-mode snapshots

Revision ID: c7d5e9f3b842
Revises: b6d4e8f2a731
Create Date: 2026-09-03

Hand-written migration — includes constraints and data backfills that Alembic
autogenerate cannot safely express.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c7d5e9f3b842"
down_revision: Union[str, None] = "b6d4e8f2a731"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "club_applications",
        sa.Column(
            "approved_payment_modes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[\"quarterly_prepaid\"]'::jsonb"),
        ),
    )
    op.add_column(
        "club_applications",
        sa.Column("transition_session_rate_kobo", sa.Integer(), nullable=True),
    )
    op.add_column(
        "club_applications",
        sa.Column("transition_expires_at", sa.Date(), nullable=True),
    )
    op.add_column(
        "club_applications",
        sa.Column("selected_payment_mode", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "ck_club_application_transition_rate_nonnegative",
        "club_applications",
        "transition_session_rate_kobo IS NULL OR transition_session_rate_kobo >= 0",
    )
    op.create_check_constraint(
        "ck_club_application_selected_payment_mode",
        "club_applications",
        "selected_payment_mode IS NULL OR selected_payment_mode IN "
        "('quarterly_prepaid', 'transition_per_session')",
    )

    op.add_column(
        "club_enrollments",
        sa.Column("pool_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "club_enrollments",
        sa.Column("operating_area_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "club_enrollments",
        sa.Column(
            "payment_mode",
            sa.String(length=32),
            nullable=False,
            server_default="quarterly_prepaid",
        ),
    )
    op.add_column(
        "club_enrollments",
        sa.Column("transition_session_rate_kobo", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE club_enrollments AS enrollment
        SET pool_id = plan.pool_id,
            operating_area_id = plan.operating_area_id
        FROM club_plan_versions AS plan
        WHERE plan.id = enrollment.plan_version_id
        """
    )
    op.create_index(
        op.f("ix_club_enrollments_pool_id"), "club_enrollments", ["pool_id"]
    )
    op.create_index(
        op.f("ix_club_enrollments_operating_area_id"),
        "club_enrollments",
        ["operating_area_id"],
    )
    op.create_check_constraint(
        "ck_club_enrollment_payment_mode",
        "club_enrollments",
        "payment_mode IN ('quarterly_prepaid', 'transition_per_session')",
    )
    op.create_check_constraint(
        "ck_club_enrollment_transition_rate",
        "club_enrollments",
        "(payment_mode = 'transition_per_session' AND "
        "transition_session_rate_kobo IS NOT NULL AND "
        "transition_session_rate_kobo >= 0) OR "
        "(payment_mode = 'quarterly_prepaid' AND "
        "transition_session_rate_kobo IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_club_enrollment_transition_rate", "club_enrollments", type_="check"
    )
    op.drop_constraint(
        "ck_club_enrollment_payment_mode", "club_enrollments", type_="check"
    )
    op.drop_index(
        op.f("ix_club_enrollments_operating_area_id"),
        table_name="club_enrollments",
    )
    op.drop_index(
        op.f("ix_club_enrollments_pool_id"), table_name="club_enrollments"
    )
    op.drop_column("club_enrollments", "transition_session_rate_kobo")
    op.drop_column("club_enrollments", "payment_mode")
    op.drop_column("club_enrollments", "operating_area_id")
    op.drop_column("club_enrollments", "pool_id")

    op.drop_constraint(
        "ck_club_application_selected_payment_mode",
        "club_applications",
        type_="check",
    )
    op.drop_constraint(
        "ck_club_application_transition_rate_nonnegative",
        "club_applications",
        type_="check",
    )
    op.drop_column("club_applications", "selected_payment_mode")
    op.drop_column("club_applications", "transition_expires_at")
    op.drop_column("club_applications", "transition_session_rate_kobo")
    op.drop_column("club_applications", "approved_payment_modes")
