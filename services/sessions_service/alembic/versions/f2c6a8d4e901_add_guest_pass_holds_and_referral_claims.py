"""add expiring guest-pass holds and referral claims

Revision ID: f2c6a8d4e901
Revises: e8c4a2f6b901
Create Date: 2026-09-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2c6a8d4e901"
down_revision: Union[str, None] = "e8c4a2f6b901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "guest_passes",
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE guest_passes
        SET reservation_expires_at = created_at + INTERVAL '30 minutes'
        WHERE status = 'pending_payment'
          AND reservation_expires_at IS NULL
        """
    )
    op.create_index(
        op.f("ix_guest_passes_reservation_expires_at"),
        "guest_passes",
        ["reservation_expires_at"],
    )

    op.create_table(
        "guest_referral_claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("guest_phone", sa.String(length=32), nullable=False),
        sa.Column("guest_pass_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("referrer_auth_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="pending", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rewarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending', 'granted', 'not_eligible')",
            name="ck_guest_referral_claim_status",
        ),
        sa.ForeignKeyConstraint(
            ["guest_pass_id"], ["guest_passes.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("guest_phone"),
        sa.UniqueConstraint(
            "guest_pass_id",
            name="uq_guest_referral_claim_guest_pass",
        ),
    )
    op.create_index(
        op.f("ix_guest_referral_claims_referrer_auth_id"),
        "guest_referral_claims",
        ["referrer_auth_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_guest_referral_claims_referrer_auth_id"),
        table_name="guest_referral_claims",
    )
    op.drop_table("guest_referral_claims")
    op.drop_index(
        op.f("ix_guest_passes_reservation_expires_at"),
        table_name="guest_passes",
    )
    op.drop_column("guest_passes", "reservation_expires_at")
