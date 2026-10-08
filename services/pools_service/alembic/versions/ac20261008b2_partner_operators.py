"""Pool-scoped reception operators.

Revision ID: ac20261008b2
Revises: ac20261008a1
"""

from alembic import op
import sqlalchemy as sa

revision = "ac20261008b2"
down_revision = "ac20261008a1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pool_access_partner_operators",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "pool_id",
            sa.UUID(),
            sa.ForeignKey("pools.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("auth_id", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pool_id", "auth_id", name="uq_pool_access_operator_scope"),
    )
    op.create_index(
        "ix_pool_access_partner_operators_pool_id",
        "pool_access_partner_operators",
        ["pool_id"],
    )
    op.create_index(
        "ix_pool_access_partner_operators_auth_id",
        "pool_access_partner_operators",
        ["auth_id"],
    )


def downgrade():
    op.drop_index(
        "ix_pool_access_partner_operators_auth_id",
        table_name="pool_access_partner_operators",
    )
    op.drop_index(
        "ix_pool_access_partner_operators_pool_id",
        table_name="pool_access_partner_operators",
    )
    op.drop_table("pool_access_partner_operators")
