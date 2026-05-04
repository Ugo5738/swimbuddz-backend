"""add_acquisition_source_to_member_profiles

Adds a structured ``acquisition_source`` enum column to ``member_profiles``.
The legacy free-form ``how_found_us`` column is preserved unchanged. The new
column is nullable so existing rows aren't broken; reporting_service uses it
for funnel-conversion breakdowns by acquisition channel.

Revision ID: c4f2a1b9d8e3
Revises: 7b25b3b337ee
Create Date: 2026-04-29 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4f2a1b9d8e3'
down_revision = '7b25b3b337ee'
branch_labels = None
depends_on = None


ACQUISITION_SOURCE_VALUES = (
    'social_instagram',
    'social_tiktok',
    'referral_member',
    'referral_friend',
    'corporate',
    'event',
    'whatsapp',
    'search',
    'other',
)


def upgrade() -> None:
    # Create the enum type explicitly so we can drop it cleanly on downgrade.
    acquisition_source_enum = sa.Enum(
        *ACQUISITION_SOURCE_VALUES,
        name='acquisition_source_enum',
    )
    acquisition_source_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        'member_profiles',
        sa.Column(
            'acquisition_source',
            sa.Enum(
                *ACQUISITION_SOURCE_VALUES,
                name='acquisition_source_enum',
                create_type=False,
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column('member_profiles', 'acquisition_source')
    sa.Enum(name='acquisition_source_enum').drop(op.get_bind(), checkfirst=True)
