"""add operating areas and effective rates

Revision ID: b71c2a95e604
Revises: 49800070bb20
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op


revision = "b71c2a95e604"
down_revision = "49800070bb20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "operating_areas",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("parent_id", sa.UUID(), nullable=True),
        sa.Column(
            "country_code", sa.String(length=2), server_default="NG", nullable=False
        ),
        sa.Column(
            "timezone",
            sa.String(length=64),
            server_default="Africa/Lagos",
            nullable=False,
        ),
        sa.Column(
            "currency", sa.String(length=3), server_default="NGN", nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["operating_areas.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "parent_id", "slug", name="uq_operating_areas_parent_slug"
        ),
    )
    op.create_index(
        op.f("ix_operating_areas_parent_id"),
        "operating_areas",
        ["parent_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operating_areas_slug"),
        "operating_areas",
        ["slug"],
        unique=False,
    )

    op.add_column(
        "pools", sa.Column("operating_area_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_pools_operating_area_id",
        "pools",
        "operating_areas",
        ["operating_area_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        op.f("ix_pools_operating_area_id"),
        "pools",
        ["operating_area_id"],
        unique=False,
    )

    op.create_table(
        "pool_rates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pool_id", sa.UUID(), nullable=False),
        sa.Column(
            "activity_scope", sa.String(length=32), server_default="all", nullable=False
        ),
        sa.Column("charge_basis", sa.String(length=32), nullable=False),
        sa.Column("amount_kobo", sa.Integer(), nullable=False),
        sa.Column(
            "currency", sa.String(length=3), server_default="NGN", nullable=False
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("starts_after", sa.Time(), nullable=True),
        sa.Column("ends_before", sa.Time(), nullable=True),
        sa.Column(
            "minimum_quantity", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
            name="ck_pool_rates_day_of_week",
        ),
        sa.ForeignKeyConstraint(["pool_id"], ["pools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pool_rates_pool_id"), "pool_rates", ["pool_id"], unique=False
    )

    op.create_table(
        "operating_cost_rates",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("operating_area_id", sa.UUID(), nullable=True),
        sa.Column("pool_id", sa.UUID(), nullable=True),
        sa.Column("supplier_name", sa.String(length=255), nullable=True),
        sa.Column(
            "activity_scope", sa.String(length=32), server_default="all", nullable=False
        ),
        sa.Column("charge_basis", sa.String(length=32), nullable=False),
        sa.Column("amount_kobo", sa.Integer(), nullable=False),
        sa.Column(
            "currency", sa.String(length=3), server_default="NGN", nullable=False
        ),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("day_of_week", sa.Integer(), nullable=True),
        sa.Column("starts_after", sa.Time(), nullable=True),
        sa.Column("ends_before", sa.Time(), nullable=True),
        sa.Column(
            "minimum_quantity", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
            name="ck_operating_cost_rates_day_of_week",
        ),
        sa.CheckConstraint(
            "NOT (operating_area_id IS NOT NULL AND pool_id IS NOT NULL)",
            name="ck_operating_cost_rates_one_scope",
        ),
        sa.ForeignKeyConstraint(
            ["operating_area_id"], ["operating_areas.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["pool_id"], ["pools.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_operating_cost_rates_category"),
        "operating_cost_rates",
        ["category"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operating_cost_rates_operating_area_id"),
        "operating_cost_rates",
        ["operating_area_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_operating_cost_rates_pool_id"),
        "operating_cost_rates",
        ["pool_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_operating_cost_rates_pool_id"),
        table_name="operating_cost_rates",
    )
    op.drop_index(
        op.f("ix_operating_cost_rates_operating_area_id"),
        table_name="operating_cost_rates",
    )
    op.drop_index(
        op.f("ix_operating_cost_rates_category"),
        table_name="operating_cost_rates",
    )
    op.drop_table("operating_cost_rates")
    op.drop_index(op.f("ix_pool_rates_pool_id"), table_name="pool_rates")
    op.drop_table("pool_rates")
    op.drop_index(op.f("ix_pools_operating_area_id"), table_name="pools")
    op.drop_constraint("fk_pools_operating_area_id", "pools", type_="foreignkey")
    op.drop_column("pools", "operating_area_id")
    op.drop_index(op.f("ix_operating_areas_slug"), table_name="operating_areas")
    op.drop_index(
        op.f("ix_operating_areas_parent_id"), table_name="operating_areas"
    )
    op.drop_table("operating_areas")
