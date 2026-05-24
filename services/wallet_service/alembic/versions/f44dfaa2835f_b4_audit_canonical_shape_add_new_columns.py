"""B4 audit canonical shape — stage 1: add new columns (nullable).

Stage 1 of 2 for the B4 wallet PR. Adds the canonical mixin columns
(`domain`, `entity_type`, `entity_id`, `actor_id`, `actor_label`) to
`wallet_audit_logs` as NULLABLE so the migration applies cleanly
against rows that don't have these fields yet.

Stage 2 (the next migration) performs the data backfill, tightens
`domain`/`entity_type`/`entity_id` to NOT NULL, converts `action` from
the audit_action_enum to a free string with namespaced values
(e.g. ``wallet.freeze``), demotes `reason` to nullable, drops the old
`wallet_id` and `performed_by` columns, and swaps the index.

Splitting is required because:
  1. The new NOT NULL columns can't be added directly while rows exist.
  2. The action column needs the old enum values present to derive the
     namespaced strings for the backfill.
  3. wallet_id and performed_by must still be readable by the backfill
     migration to source the new columns.

Hand-written migration — autogenerate also captured unrelated drift
from `reward_notification_preferences` (index rename) and
`wallet_events` (unique constraint drop). Those are separate concerns
and removed from this migration. The wallet-audit changes themselves
are autogen output, narrowed.

Revision ID: f44dfaa2835f
Revises: c4d5e6f7a801
Create Date: 2026-05-24 07:35:22.625481
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "f44dfaa2835f"
down_revision = "c4d5e6f7a801"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "wallet_audit_logs",
        sa.Column("domain", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "wallet_audit_logs",
        sa.Column("entity_type", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "wallet_audit_logs",
        sa.Column("entity_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "wallet_audit_logs",
        sa.Column("actor_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "wallet_audit_logs",
        sa.Column("actor_label", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("wallet_audit_logs", "actor_label")
    op.drop_column("wallet_audit_logs", "actor_id")
    op.drop_column("wallet_audit_logs", "entity_id")
    op.drop_column("wallet_audit_logs", "entity_type")
    op.drop_column("wallet_audit_logs", "domain")
