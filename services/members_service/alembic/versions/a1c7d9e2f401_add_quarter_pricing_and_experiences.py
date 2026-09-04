"""add quarter pricing and contextual Community Experiences

Revision ID: a1c7d9e2f401
Revises: f4b8c2d9e601
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1c7d9e2f401"
down_revision: Union[str, None] = "f4b8c2d9e601"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "community_experience_offerings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column(
            "currency", sa.String(length=8), server_default="NGN", nullable=False
        ),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column(
            "standard_member_fee_kobo",
            sa.Integer(),
            server_default="5000000",
            nullable=False,
        ),
        sa.Column(
            "club_member_fee_kobo",
            sa.Integer(),
            server_default="4000000",
            nullable=False,
        ),
        sa.Column(
            "club_bundle_fee_kobo",
            sa.Integer(),
            server_default="3000000",
            nullable=False,
        ),
        sa.Column("purchase_opens_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purchase_closes_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "period_end >= period_start", name="ck_community_experience_period"
        ),
        sa.CheckConstraint(
            "standard_member_fee_kobo >= 0 AND club_member_fee_kobo >= 0 "
            "AND club_bundle_fee_kobo >= 0",
            name="ck_community_experience_fees_nonnegative",
        ),
        sa.CheckConstraint(
            "club_bundle_fee_kobo <= club_member_fee_kobo "
            "AND club_member_fee_kobo <= standard_member_fee_kobo",
            name="ck_community_experience_price_ladder",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "club_plan_versions",
        sa.Column(
            "community_experience_offering_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "club_plan_versions", sa.Column("period_start", sa.Date(), nullable=True)
    )
    op.add_column(
        "club_plan_versions", sa.Column("period_end", sa.Date(), nullable=True)
    )
    op.add_column(
        "club_plan_versions",
        sa.Column(
            "minimum_entry_sessions", sa.Integer(), server_default="5", nullable=False
        ),
    )
    op.execute(
        """
        UPDATE club_plan_versions
        SET period_start = effective_from,
            period_end = COALESCE(effective_to, (effective_from + INTERVAL '3 months - 1 day')::date)
        """
    )
    op.alter_column("club_plan_versions", "period_start", nullable=False)
    op.alter_column("club_plan_versions", "period_end", nullable=False)
    op.create_foreign_key(
        "fk_club_plan_experience_offering",
        "club_plan_versions",
        "community_experience_offerings",
        ["community_experience_offering_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_club_plan_versions_community_experience_offering_id",
        "club_plan_versions",
        ["community_experience_offering_id"],
    )
    op.create_check_constraint(
        "ck_club_plan_minimum_entry_sessions",
        "club_plan_versions",
        "minimum_entry_sessions > 0 AND minimum_entry_sessions <= sessions_included",
    )
    op.create_check_constraint(
        "ck_club_plan_service_period",
        "club_plan_versions",
        "period_end >= period_start",
    )

    op.create_table(
        "club_application_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("application_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["club_applications.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id"], ["club_plan_versions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "application_id",
            "plan_version_id",
            name="uq_club_application_plan_selection",
        ),
    )
    op.create_index(
        "ix_club_application_plans_application_id",
        "club_application_plans",
        ["application_id"],
    )
    op.execute(
        """
        INSERT INTO club_application_plans (
            id, application_id, plan_version_id, sort_order, created_at
        )
        SELECT gen_random_uuid(), id, plan_version_id, 0, created_at
        FROM club_applications
        """
    )

    op.drop_constraint(
        "club_enrollments_application_id_key", "club_enrollments", type_="unique"
    )
    op.create_unique_constraint(
        "uq_club_enrollment_application_plan",
        "club_enrollments",
        ["application_id", "plan_version_id"],
    )

    op.create_table(
        "community_experience_purchases",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("offering_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("club_enrollment_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("price_context", sa.String(length=32), nullable=False),
        sa.Column("amount_paid_kobo", sa.Integer(), nullable=False),
        sa.Column("payment_reference", sa.String(length=128), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default="active", nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_paid_kobo >= 0", name="ck_community_experience_purchase_amount"
        ),
        sa.ForeignKeyConstraint(["member_id"], ["members.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["offering_id"], ["community_experience_offerings.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["club_enrollment_id"], ["club_enrollments.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "member_id", "offering_id", name="uq_community_experience_member_offering"
        ),
    )
    op.create_index(
        "ix_community_experience_purchases_member_id",
        "community_experience_purchases",
        ["member_id"],
    )
    op.create_index(
        "ix_community_experience_purchases_offering_id",
        "community_experience_purchases",
        ["offering_id"],
    )
    op.create_index(
        "ix_community_experience_purchases_payment_reference",
        "community_experience_purchases",
        ["payment_reference"],
    )


def downgrade() -> None:
    op.drop_table("community_experience_purchases")
    op.drop_constraint(
        "uq_club_enrollment_application_plan", "club_enrollments", type_="unique"
    )
    op.create_unique_constraint(
        "club_enrollments_application_id_key", "club_enrollments", ["application_id"]
    )
    op.drop_table("club_application_plans")
    op.drop_constraint(
        "ck_club_plan_service_period", "club_plan_versions", type_="check"
    )
    op.drop_constraint(
        "ck_club_plan_minimum_entry_sessions", "club_plan_versions", type_="check"
    )
    op.drop_index(
        "ix_club_plan_versions_community_experience_offering_id",
        table_name="club_plan_versions",
    )
    op.drop_constraint(
        "fk_club_plan_experience_offering", "club_plan_versions", type_="foreignkey"
    )
    op.drop_column("club_plan_versions", "minimum_entry_sessions")
    op.drop_column("club_plan_versions", "period_end")
    op.drop_column("club_plan_versions", "period_start")
    op.drop_column("club_plan_versions", "community_experience_offering_id")
    op.drop_table("community_experience_offerings")
