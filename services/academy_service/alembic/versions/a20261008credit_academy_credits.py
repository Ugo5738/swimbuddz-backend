"""Academy tuition credit and transfer provenance.

Revision ID: a20261008credit
Revises: aa20261008merge
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a20261008credit"
down_revision = "aa20261008merge"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("academy_financial_credits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("enrollment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enrollments.id"), nullable=False),
        sa.Column("source_enrollment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("enrollments.id"), nullable=True),
        sa.Column("source_reference", sa.String(200), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(24), nullable=False),
        sa.Column("origin_credit_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("academy_financial_credits.id"), nullable=True),
        sa.Column("amount_kobo", sa.BigInteger(), nullable=False),
        sa.Column("actor_auth_id", sa.String(160), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_kobo > 0", name="ck_academy_financial_credit_positive"),
        sa.UniqueConstraint("source_reference", name="uq_academy_financial_credit_source"))
    op.create_index("ix_academy_financial_credits_enrollment_id", "academy_financial_credits", ["enrollment_id"])

def downgrade():
    op.drop_index("ix_academy_financial_credits_enrollment_id", table_name="academy_financial_credits")
    op.drop_table("academy_financial_credits")
