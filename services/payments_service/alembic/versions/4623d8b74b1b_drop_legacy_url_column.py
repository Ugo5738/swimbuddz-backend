"""drop_legacy_url_column

Revision ID: 4623d8b74b1b
Revises: efe570a06fcc
Create Date: 2026-01-09 18:50:04.025031
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '4623d8b74b1b'
down_revision = 'efe570a06fcc'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop legacy _url column now that we use _media_id reference
    op.drop_column('payments', 'proof_of_payment_url')


def downgrade() -> None:
    # Re-add legacy _url column
    op.add_column('payments', sa.Column('proof_of_payment_url', sa.String(length=512), nullable=True))

