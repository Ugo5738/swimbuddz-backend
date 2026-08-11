"""add configurable payment charges

Revision ID: b7d3e9a4c210
Revises: a91c4e7d2b60
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b7d3e9a4c210"
down_revision: Union[str, None] = "a91c4e7d2b60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "additional_charge_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("payment_method", sa.String(length=32), nullable=True),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("rate_basis_points", sa.Integer(), server_default="0", nullable=False),
        sa.Column("fixed_amount_kobo", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cap_amount_kobo", sa.Integer(), nullable=True),
        sa.Column("waive_fixed_below_kobo", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by_auth_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rate_basis_points >= 0", name="ck_charge_policy_rate_nonnegative"),
        sa.CheckConstraint("fixed_amount_kobo >= 0", name="ck_charge_policy_fixed_nonnegative"),
        sa.CheckConstraint(
            "cap_amount_kobo IS NULL OR cap_amount_kobo >= 0",
            name="ck_charge_policy_cap_nonnegative",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "purpose", "payment_method", "label", name="uq_charge_policy_scope_label"
        ),
    )
    op.create_index(
        "ix_additional_charge_policies_purpose",
        "additional_charge_policies",
        ["purpose"],
    )
    op.create_index(
        "uq_charge_policy_scope_label_nullsafe",
        "additional_charge_policies",
        ["purpose", sa.text("COALESCE(payment_method, '')"), "label"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("additional_charge_policies")
