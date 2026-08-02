"""add event templates and import keys

Revision ID: 6f1a8b3c4d20
Revises: 5ad1c7e492b1
Create Date: 2026-08-02
"""

import sqlalchemy as sa
from alembic import op


revision = "6f1a8b3c4d20"
down_revision = "5ad1c7e492b1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_templates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("audience", sa.String(), server_default="community", nullable=False),
        sa.Column("visibility", sa.String(), server_default="public", nullable=False),
        sa.Column("location_type", sa.String(), server_default="physical", nullable=False),
        sa.Column("timezone", sa.String(), server_default="Africa/Lagos", nullable=False),
        sa.Column("location_area", sa.String(), nullable=True),
        sa.Column("is_location_private", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("location", sa.String(), nullable=True),
        sa.Column("local_start_time", sa.Time(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("max_capacity", sa.Integer(), nullable=True),
        sa.Column("tier_access", sa.String(), server_default="community", nullable=False),
        sa.Column("pool_id", sa.UUID(), nullable=True),
        sa.Column("cost_kobo", sa.Integer(), nullable=True),
        sa.Column("frequency", sa.String(), nullable=False),
        sa.Column("interval", sa.Integer(), server_default="1", nullable=False),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("week_of_month", sa.Integer(), nullable=True),
        sa.Column("day_of_month", sa.Integer(), nullable=True),
        sa.Column("month_of_year", sa.Integer(), nullable=True),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_by", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("events", sa.Column("template_id", sa.UUID(), nullable=True))
    op.add_column("events", sa.Column("external_key", sa.String(), nullable=True))
    op.create_foreign_key(
        "fk_events_template_id_event_templates",
        "events",
        "event_templates",
        ["template_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_events_template_id"), "events", ["template_id"], unique=False)
    op.create_index(op.f("ix_events_external_key"), "events", ["external_key"], unique=True)


def downgrade() -> None:
    op.drop_index(op.f("ix_events_external_key"), table_name="events")
    op.drop_index(op.f("ix_events_template_id"), table_name="events")
    op.drop_constraint("fk_events_template_id_event_templates", "events", type_="foreignkey")
    op.drop_column("events", "external_key")
    op.drop_column("events", "template_id")
    op.drop_table("event_templates")
