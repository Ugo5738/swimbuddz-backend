"""enforce invite visibility and attendance access

Revision ID: c7b9d1e3f526
Revises: f6a8c0d2e415
Create Date: 2026-09-23
"""

from alembic import op

revision = "c7b9d1e3f526"
down_revision = "f6a8c0d2e415"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Privacy wins when repairing malformed legacy combinations. Anything
    # hidden or invite-scoped becomes consistently invite-only on both axes.
    op.execute(
        "UPDATE events SET visibility = 'invite_only', tier_access = 'invite_only' "
        "WHERE visibility = 'invite_only' OR tier_access = 'invite_only'"
    )
    op.execute(
        "UPDATE event_templates SET visibility = 'invite_only', "
        "tier_access = 'invite_only' "
        "WHERE visibility = 'invite_only' OR tier_access = 'invite_only'"
    )
    op.create_check_constraint(
        "ck_events_invite_visibility_access",
        "events",
        "(visibility = 'invite_only') = (tier_access = 'invite_only')",
    )
    op.create_check_constraint(
        "ck_event_templates_invite_visibility_access",
        "event_templates",
        "(visibility = 'invite_only') = (tier_access = 'invite_only')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_event_templates_invite_visibility_access",
        "event_templates",
        type_="check",
    )
    op.drop_constraint(
        "ck_events_invite_visibility_access",
        "events",
        type_="check",
    )
