"""add_media_service_tables

Revision ID: a1b2c3d4e5f6
Revises: 699858cde72f
Create Date: 2025-11-26 02:56:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '699858cde72f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema - Add media service tables."""
    
    # Create albums table
    op.create_table(
        'albums',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('album_type', sa.String(), nullable=False),
        sa.Column('linked_entity_id', UUID(as_uuid=True), nullable=True),
        sa.Column('cover_photo_id', UUID(as_uuid=True), nullable=True),
        sa.Column('created_by', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create photos table
    op.create_table(
        'photos',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('album_id', UUID(as_uuid=True), nullable=False),
        sa.Column('file_url', sa.String(), nullable=False),
        sa.Column('thumbnail_url', sa.String(), nullable=True),
        sa.Column('caption', sa.Text(), nullable=True),
        sa.Column('taken_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('uploaded_by', UUID(as_uuid=True), nullable=False),
        sa.Column('is_featured', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create photo_tags table
    op.create_table(
        'photo_tags',
        sa.Column('id', UUID(as_uuid=True), nullable=False),
        sa.Column('photo_id', UUID(as_uuid=True), nullable=False),
        sa.Column('member_id', UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    
    # Create indexes for performance
    op.create_index('ix_albums_album_type', 'albums', ['album_type'])
    op.create_index('ix_albums_created_at', 'albums', ['created_at'])
    op.create_index('ix_photos_album_id', 'photos', ['album_id'])
    op.create_index('ix_photos_is_featured', 'photos', ['is_featured'])
    op.create_index('ix_photo_tags_photo_id', 'photo_tags', ['photo_id'])
    op.create_index('ix_photo_tags_member_id', 'photo_tags', ['member_id'])


def downgrade() -> None:
    """Downgrade schema - Remove media service tables."""
    
    # Drop indexes
    op.drop_index('ix_photo_tags_member_id', table_name='photo_tags')
    op.drop_index('ix_photo_tags_photo_id', table_name='photo_tags')
    op.drop_index('ix_photos_is_featured', table_name='photos')
    op.drop_index('ix_photos_album_id', table_name='photos')
    op.drop_index('ix_albums_created_at', table_name='albums')
    op.drop_index('ix_albums_album_type', table_name='albums')
    
    # Drop tables
    op.drop_table('photo_tags')
    op.drop_table('photos')
    op.drop_table('albums')
