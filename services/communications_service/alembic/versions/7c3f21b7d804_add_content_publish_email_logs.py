"""add_content_publish_email_logs

Revision ID: 7c3f21b7d804
Revises: 785e73dd9714
Create Date: 2026-07-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7c3f21b7d804'
down_revision = '785e73dd9714'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'content_posts',
        sa.Column('email_on_publish', sa.Boolean(), nullable=False, server_default='false'),
    )
    op.create_table(
        'content_post_email_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('post_id', sa.UUID(), nullable=False),
        sa.Column('member_id', sa.UUID(), nullable=False),
        sa.Column('channel', sa.String(), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('delivery_status', sa.String(), nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'post_id',
            'member_id',
            'channel',
            name='uq_content_post_email_logs_post_member_channel',
        ),
    )
    op.create_index(
        op.f('ix_content_post_email_logs_member_id'),
        'content_post_email_logs',
        ['member_id'],
        unique=False,
    )
    op.create_index(
        op.f('ix_content_post_email_logs_post_id'),
        'content_post_email_logs',
        ['post_id'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_content_post_email_logs_post_id'),
        table_name='content_post_email_logs',
    )
    op.drop_index(
        op.f('ix_content_post_email_logs_member_id'),
        table_name='content_post_email_logs',
    )
    op.drop_table('content_post_email_logs')
    op.drop_column('content_posts', 'email_on_publish')
