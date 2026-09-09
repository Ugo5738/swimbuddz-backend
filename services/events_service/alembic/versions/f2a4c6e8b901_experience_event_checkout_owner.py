"""Link Event checkout ownership to a separate Community Experience.

Revision ID: f2a4c6e8b901
Revises: 71c4e9a2b830
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from alembic import op

revision = "f2a4c6e8b901"
down_revision = "71c4e9a2b830"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "events",
        sa.Column(
            "community_experience_offering_id", pg.UUID(as_uuid=True), nullable=True
        ),
    )
    op.create_index(
        "ix_events_community_experience_offering_id",
        "events",
        ["community_experience_offering_id"],
    )
    op.add_column(
        "event_reminder_logs",
        sa.Column("participant_id", pg.UUID(as_uuid=True), nullable=True),
    )
    op.alter_column("event_reminder_logs", "member_id", nullable=True)
    op.create_unique_constraint(
        "uq_event_reminder_participant_offset",
        "event_reminder_logs",
        ["event_id", "participant_id", "reminder_hours"],
    )


def downgrade():
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM event_reminder_logs WHERE participant_id IS NOT NULL) OR EXISTS (SELECT 1 FROM events WHERE community_experience_offering_id IS NOT NULL) THEN RAISE EXCEPTION 'Reconcile Experience Events and participant reminders before downgrade'; END IF; END $$"
    )
    op.drop_constraint(
        "uq_event_reminder_participant_offset", "event_reminder_logs", type_="unique"
    )
    op.alter_column("event_reminder_logs", "member_id", nullable=False)
    op.drop_column("event_reminder_logs", "participant_id")
    op.drop_index("ix_events_community_experience_offering_id", table_name="events")
    op.drop_column("events", "community_experience_offering_id")
