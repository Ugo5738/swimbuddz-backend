"""add_session_volunteer_shift_times

Revision ID: 0084cb5dc34f
Revises: 18f8804a6300
Create Date: 2026-08-11 13:07:39.320775

Hand-written migration — the configured development Supabase tenant was
unavailable to Alembic autogenerate. These two additive nullable columns
directly mirror ``SessionTemplateVolunteerSlot``.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0084cb5dc34f'
down_revision = '18f8804a6300'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "session_template_volunteer_slots",
        sa.Column("start_time_override", sa.Time(), nullable=True),
    )
    op.add_column(
        "session_template_volunteer_slots",
        sa.Column("end_time_override", sa.Time(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session_template_volunteer_slots", "end_time_override")
    op.drop_column("session_template_volunteer_slots", "start_time_override")
