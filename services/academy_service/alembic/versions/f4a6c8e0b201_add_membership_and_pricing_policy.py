"""add Academy membership and internal pricing policy

Revision ID: f4a6c8e0b201
Revises: c8d4e6f7a901
Create Date: 2026-08-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "f4a6c8e0b201"
down_revision = "c8d4e6f7a901"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "programs",
        sa.Column(
            "membership_policy",
            sa.String(length=24),
            nullable=False,
            server_default="open",
        ),
    )
    op.add_column(
        "cohorts",
        sa.Column("membership_policy_override", sa.String(length=24), nullable=True),
    )
    op.add_column(
        "cohorts",
        sa.Column(
            "pricing_cost_lines",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "cohorts",
        sa.Column(
            "pricing_margin_basis_points",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "cohorts", sa.Column("calculated_price_kobo", sa.Integer(), nullable=True)
    )
    op.add_column(
        "cohorts", sa.Column("suggested_price_kobo", sa.Integer(), nullable=True)
    )
    op.add_column(
        "cohorts",
        sa.Column(
            "pricing_round_to_kobo",
            sa.Integer(),
            nullable=False,
            server_default="500000",
        ),
    )
    op.create_check_constraint(
        "ck_programs_membership_policy",
        "programs",
        "membership_policy IN ('open', 'active_required', 'included')",
    )
    op.create_check_constraint(
        "ck_cohorts_membership_policy_override",
        "cohorts",
        "membership_policy_override IS NULL OR membership_policy_override IN "
        "('open', 'active_required', 'included')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_cohorts_membership_policy_override", "cohorts", type_="check"
    )
    op.drop_constraint("ck_programs_membership_policy", "programs", type_="check")
    op.drop_column("cohorts", "pricing_round_to_kobo")
    op.drop_column("cohorts", "suggested_price_kobo")
    op.drop_column("cohorts", "calculated_price_kobo")
    op.drop_column("cohorts", "pricing_margin_basis_points")
    op.drop_column("cohorts", "pricing_cost_lines")
    op.drop_column("cohorts", "membership_policy_override")
    op.drop_column("programs", "membership_policy")
