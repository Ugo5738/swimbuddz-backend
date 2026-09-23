"""enforce legacy audience mirror

Revision ID: f6a8c0d2e415
Revises: d4e6f8a0b213
Create Date: 2026-09-23
"""

from alembic import op

revision = "f6a8c0d2e415"
down_revision = "d4e6f8a0b213"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Repair any out-of-band writes before making the staged compatibility
    # mirror an actual database invariant.
    op.execute(
        "UPDATE events SET audience = primary_audience WHERE audience IS DISTINCT FROM primary_audience"
    )
    op.execute(
        "UPDATE event_templates SET audience = primary_audience WHERE audience IS DISTINCT FROM primary_audience"
    )
    op.create_check_constraint(
        "ck_events_audience_mirrors_primary",
        "events",
        "audience = primary_audience",
    )
    op.create_check_constraint(
        "ck_event_templates_audience_mirrors_primary",
        "event_templates",
        "audience = primary_audience",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_event_templates_audience_mirrors_primary",
        "event_templates",
        type_="check",
    )
    op.drop_constraint(
        "ck_events_audience_mirrors_primary",
        "events",
        type_="check",
    )
