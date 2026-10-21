"""Explicit public pool media and consumer-focused SwimBuddz assessment.

Revision ID: ac20261021h8
Revises: ac20261021g7
"""
import sqlalchemy as sa
from alembic import op

revision = "ac20261021h8"
down_revision = "ac20261021g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pool_assets", sa.Column("directory_public", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("pools", sa.Column("directory_assessment_rating", sa.Integer(), nullable=True))
    op.add_column("pools", sa.Column("directory_assessment_summary", sa.Text(), nullable=True))
    op.add_column("pools", sa.Column("directory_assessment_updated_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_directory_assessment_rating", "pools", "directory_assessment_rating BETWEEN 1 AND 5")


def downgrade() -> None:
    op.drop_constraint("ck_directory_assessment_rating", "pools", type_="check")
    op.drop_column("pools", "directory_assessment_updated_at")
    op.drop_column("pools", "directory_assessment_summary")
    op.drop_column("pools", "directory_assessment_rating")
    op.drop_column("pool_assets", "directory_public")
