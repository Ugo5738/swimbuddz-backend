"""add location-specific Club onboarding

Revision ID: f4b8c2d9e601
Revises: d7a4f9c2e8b1
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4b8c2d9e601"
down_revision: Union[str, None] = "d7a4f9c2e8b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clubs",
        sa.Column("operating_area_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_clubs_operating_area_id", "clubs", ["operating_area_id"])

    op.create_table(
        "club_plan_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("club_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("billing_cycle", sa.String(length=24), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("club_fee_kobo", sa.Integer(), nullable=False),
        sa.Column(
            "community_experience_fee_kobo",
            sa.Integer(),
            server_default="3000000",
            nullable=False,
        ),
        sa.Column(
            "community_experience_default_selected",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("sessions_included", sa.Integer(), server_default="12", nullable=False),
        sa.Column(
            "refreshments_included",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("premium_venue_note", sa.Text(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("club_fee_kobo >= 0", name="ck_club_plan_fee_nonnegative"),
        sa.CheckConstraint(
            "community_experience_fee_kobo >= 0",
            name="ck_club_plan_experience_fee_nonnegative",
        ),
        sa.CheckConstraint("sessions_included > 0", name="ck_club_plan_sessions_positive"),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_club_plan_versions_club_id", "club_plan_versions", ["club_id"])
    op.create_index(
        "ix_club_plan_versions_active_period",
        "club_plan_versions",
        ["club_id", "is_active", "effective_from"],
    )

    op.create_table(
        "club_applications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("club_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="assessment_required", nullable=False),
        sa.Column(
            "community_experience_selected",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("preferred_pod_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_version_id"], ["club_plan_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["preferred_pod_id"], ["pods.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_club_applications_member_id", "club_applications", ["member_id"])
    op.create_index("ix_club_applications_club_id", "club_applications", ["club_id"])
    op.create_index(
        "ix_club_applications_member_status", "club_applications", ["member_id", "status"]
    )

    op.create_table(
        "club_readiness_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("self_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("observed_checks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("assessor_member_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("outcome", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("nonstop_distance_m", sa.Integer(), nullable=True),
        sa.Column("deep_water_comfort", sa.String(length=32), nullable=True),
        sa.Column("primary_technique_focus", sa.Text(), nullable=True),
        sa.Column("first_club_milestone", sa.Text(), nullable=True),
        sa.Column("assessor_notes", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["club_applications.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["assessor_member_id"], ["members.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id"),
    )

    op.create_table(
        "club_enrollments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("club_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_reference", sa.String(length=128), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assigned_pod_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="active", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("ends_at > starts_at", name="ck_club_enrollment_period"),
        sa.ForeignKeyConstraint(["application_id"], ["club_applications.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assigned_pod_id"], ["pods.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["club_id"], ["clubs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_version_id"], ["club_plan_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("application_id"),
    )
    op.create_index("ix_club_enrollments_member_id", "club_enrollments", ["member_id"])
    op.create_index("ix_club_enrollments_club_id", "club_enrollments", ["club_id"])
    op.create_index("ix_club_enrollments_payment_reference", "club_enrollments", ["payment_reference"])
    op.create_index(
        "ix_club_enrollments_member_active",
        "club_enrollments",
        ["member_id", "status", "ends_at"],
    )


def downgrade() -> None:
    op.drop_table("club_enrollments")
    op.drop_table("club_readiness_assessments")
    op.drop_table("club_applications")
    op.drop_table("club_plan_versions")
    op.drop_index("ix_clubs_operating_area_id", table_name="clubs")
    op.drop_column("clubs", "operating_area_id")
