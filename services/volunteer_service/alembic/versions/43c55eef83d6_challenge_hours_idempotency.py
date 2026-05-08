"""challenge_hours_idempotency

Adds external_reference_id to volunteer_hours_log for cross-service
idempotency (e.g. members_service writes the challenge submission_id here
when crediting hours for an approved attempt).

A partial unique index on (source, external_reference_id, member_id)
prevents concurrent retries from double-crediting. Alembic autogen does
not detect partial indexes, so the index op is added by hand below.

Revision ID: 43c55eef83d6
Revises: 8a840b164e5f
Create Date: 2026-05-07 02:03:45.412405
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '43c55eef83d6'
down_revision = '8a840b164e5f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'volunteer_hours_log',
        sa.Column('external_reference_id', sa.String(length=64), nullable=True),
    )
    # Partial unique index — only enforced when external_reference_id is set,
    # so legacy slot_completion / manual_entry rows (which leave it NULL)
    # don't bump into uniqueness errors.
    op.create_index(
        'uq_volunteer_hours_external_ref',
        'volunteer_hours_log',
        ['source', 'external_reference_id', 'member_id'],
        unique=True,
        postgresql_where=sa.text('external_reference_id IS NOT NULL'),
    )


def downgrade() -> None:
    op.drop_index(
        'uq_volunteer_hours_external_ref',
        table_name='volunteer_hours_log',
    )
    op.drop_column('volunteer_hours_log', 'external_reference_id')
