"""Add treasury liability_settlement support and opaque evidence_media_id columns.

Revision ID: a23c01e4b801
Revises: feab17112601
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "a23c01e4b801"
down_revision = "feab17112601"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "treasury_bank_accounts",
        sa.Column("statement_evidence_media_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "treasury_requests",
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "treasury_bank_snapshots",
        sa.Column("evidence_media_id", UUID(as_uuid=True), nullable=True),
    )

    op.alter_column(
        "treasury_requests",
        "kind",
        existing_type=sa.String(20),
        type_=sa.String(30),
        existing_nullable=False,
    )
    op.alter_column(
        "treasury_budgets",
        "kind",
        existing_type=sa.String(20),
        type_=sa.String(30),
        existing_nullable=False,
    )

    op.drop_constraint("ck_treasury_request_kind", "treasury_requests", type_="check")
    op.create_check_constraint(
        "ck_treasury_request_kind",
        "treasury_requests",
        "kind IN ('expense','funding','liability_settlement')",
    )
    op.drop_constraint("ck_treasury_budget_kind", "treasury_budgets", type_="check")
    op.create_check_constraint(
        "ck_treasury_budget_kind",
        "treasury_budgets",
        "kind IN ('expense','funding','liability_settlement')",
    )


def downgrade():
    op.drop_constraint("ck_treasury_budget_kind", "treasury_budgets", type_="check")
    op.create_check_constraint(
        "ck_treasury_budget_kind",
        "treasury_budgets",
        "kind IN ('expense','funding')",
    )
    op.drop_constraint("ck_treasury_request_kind", "treasury_requests", type_="check")
    op.create_check_constraint(
        "ck_treasury_request_kind",
        "treasury_requests",
        "kind IN ('expense','funding')",
    )
    op.alter_column(
        "treasury_budgets",
        "kind",
        existing_type=sa.String(30),
        type_=sa.String(20),
        existing_nullable=False,
    )
    op.alter_column(
        "treasury_requests",
        "kind",
        existing_type=sa.String(30),
        type_=sa.String(20),
        existing_nullable=False,
    )
    op.drop_column("treasury_bank_snapshots", "evidence_media_id")
    op.drop_column("treasury_requests", "evidence_media_id")
    op.drop_column("treasury_bank_accounts", "statement_evidence_media_id")
