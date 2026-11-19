"""Create member swim plans and a configurable publication policy.

Revision ID: 9b7c4d2e6105
Revises: c7b9d1e3f526
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from alembic import op

revision = "9b7c4d2e6105"
down_revision = "c7b9d1e3f526"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "swim_publication_policies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("auto_approve_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by", sa.String(160)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.execute(
        "INSERT INTO swim_publication_policies (id, auto_approve_public, updated_at) "
        "VALUES (1, true, now()) ON CONFLICT DO NOTHING"
    )
    op.create_table(
        "swim_plans",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("member_id", pg.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("notes", sa.String(1000)),
        sa.Column("pool_id", pg.UUID(as_uuid=True)),
        sa.Column("location", sa.String(255)),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("participants", sa.Integer(), nullable=False),
        sa.Column("coaching_focus", sa.String(250)),
        sa.Column("approval_status", sa.String(16), nullable=False),
        sa.Column("venue_status", sa.String(20), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("linked_event_id", pg.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="SET NULL")),
        sa.Column("admin_notes", sa.String(1000)),
        sa.Column("verified_by", sa.String(160)),
        sa.Column("venue_confirmation_reference", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("participants BETWEEN 1 AND 25", name="ck_swim_plans_participants"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_swim_plans_time_order"),
        sa.CheckConstraint("kind IN ('solo','friends','open','coached')", name="ck_swim_plans_kind"),
        sa.CheckConstraint("approval_status IN ('approved','pending','rejected')", name="ck_swim_plans_approval"),
        sa.CheckConstraint(
            "venue_status IN ('pending','verified','not_required','rejected')",
            name="ck_swim_plans_venue",
        ),
    )
    op.create_table(
        "swim_plan_audit",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "plan_id", pg.UUID(as_uuid=True),
            sa.ForeignKey("swim_plans.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("actor_auth_id", sa.String(160), nullable=False),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("old_value", sa.String(1000)),
        sa.Column("new_value", sa.String(1000)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_swim_plan_audit_plan_id", "swim_plan_audit", ["plan_id"])


def downgrade():
    op.execute(
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM swim_plans) OR EXISTS (SELECT 1 FROM swim_plan_audit) "
        "THEN RAISE EXCEPTION 'Swim plan history must be archived before downgrade'; "
        "END IF; END $$"
    )
    op.drop_index("ix_swim_plan_audit_plan_id", table_name="swim_plan_audit")
    op.drop_table("swim_plan_audit")
    op.drop_table("swim_plans")
    op.drop_table("swim_publication_policies")
