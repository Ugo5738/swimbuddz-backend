"""Track purchased Academy lessons and per-session student allocation.

Revision ID: a20261025class
Revises: a20261023quiz
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a20261025class"
down_revision = "a20261023quiz"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "academy_class_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enrollments.id"), nullable=False, unique=True),
        sa.Column("purchased_classes", sa.Integer(), nullable=False),
        sa.Column("opening_consumed", sa.Integer(), nullable=False),
        sa.Column("opening_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_reason", sa.Text(), nullable=False),
        sa.Column("baseline_actor_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("purchased_classes > 0 AND opening_consumed >= 0 AND opening_consumed <= purchased_classes", name="ck_academy_class_plan_balance"),
    )
    op.create_index("ix_academy_class_plans_enrollment_id", "academy_class_plans", ["enrollment_id"])
    op.create_table(
        "academy_class_allocations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enrollments.id"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("agreed_fee_kobo", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("booking_id", postgresql.UUID(as_uuid=True)),
        sa.Column("actor_auth_id", sa.String(160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("enrollment_id", "session_id", name="uq_academy_class_allocations_enrollment_session"),
        sa.CheckConstraint("kind IN ('prepaid','paid_extra','makeup','support')", name="ck_academy_class_allocation_kind"),
        sa.CheckConstraint("status IN ('reserved','applied','reversed')", name="ck_academy_class_allocation_status"),
        sa.CheckConstraint("agreed_fee_kobo >= 0", name="ck_academy_class_allocation_fee"),
    )
    op.create_index("ix_academy_class_allocations_enrollment_id", "academy_class_allocations", ["enrollment_id"])
    op.create_index("ix_academy_class_allocations_session_id", "academy_class_allocations", ["session_id"])


def downgrade():
    op.drop_index("ix_academy_class_allocations_session_id", table_name="academy_class_allocations")
    op.drop_index("ix_academy_class_allocations_enrollment_id", table_name="academy_class_allocations")
    op.drop_table("academy_class_allocations")
    op.drop_index("ix_academy_class_plans_enrollment_id", table_name="academy_class_plans")
    op.drop_table("academy_class_plans")
