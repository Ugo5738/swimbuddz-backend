"""drop_legacy_url_columns

Revision ID: 12bb9b1e6cfa
Revises: 6be39f69482b
Create Date: 2026-01-09 18:48:07.530634
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '12bb9b1e6cfa'
down_revision = '6be39f69482b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop legacy _url columns now that we use _media_id references
    op.drop_column('members', 'profile_photo_url')
    op.drop_column('coach_profiles', 'coach_profile_photo_url')
    op.drop_column('coach_profiles', 'background_check_document_url')


def downgrade() -> None:
    # Re-add legacy _url columns
    op.add_column('coach_profiles', sa.Column('background_check_document_url', sa.String(length=512), nullable=True))
    op.add_column('coach_profiles', sa.Column('coach_profile_photo_url', sa.String(length=512), nullable=True))
    op.add_column('members', sa.Column('profile_photo_url', sa.String(length=512), nullable=True))

