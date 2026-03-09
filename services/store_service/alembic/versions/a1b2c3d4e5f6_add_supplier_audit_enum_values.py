"""Add supplier and supplier_payout to audit entity type enum."""

revision = "a1b2c3d4e5f6"
down_revision = "3cb5a77a69fe"
branch_labels = None
depends_on = None

from alembic import op


def upgrade() -> None:
    # Add missing enum values to store_audit_entity_type_enum
    op.execute("ALTER TYPE store_audit_entity_type_enum ADD VALUE IF NOT EXISTS 'supplier'")
    op.execute("ALTER TYPE store_audit_entity_type_enum ADD VALUE IF NOT EXISTS 'supplier_payout'")


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    # The values are harmless if left in place.
    pass
