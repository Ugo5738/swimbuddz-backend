"""Add Store post-purchase order lifecycle, tender fields, and cases (ADR-023).

Hand-written migration — adds separated payment/fulfillment status enums and
columns on store_orders, sourcing/net-price snapshots on store_order_items,
transaction_type on store_credit_transactions, and the store_order_cases /
store_order_case_items tables with CHECK constraints and indexes.

Revision ID: g20261125ord
Revises: f181126a7302
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "g20261125ord"
down_revision = "f181126a7302"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    payment_status_enum = postgresql.ENUM(
        "unpaid",
        "pending_review",
        "paid",
        "partially_refunded",
        "refunded",
        "failed",
        name="store_order_payment_status_enum",
        create_type=False,
    )
    fulfillment_status_enum = postgresql.ENUM(
        "not_started",
        "awaiting_preparation",
        "processing",
        "ready_for_pickup",
        "picked_up",
        "shipped",
        "delivered",
        "cancelled",
        "returned",
        name="store_order_fulfillment_status_enum",
        create_type=False,
    )
    case_type_enum = postgresql.ENUM(
        "cancel_request",
        "variant_swap",
        "return_exchange",
        "defect_claim",
        name="store_order_case_type_enum",
        create_type=False,
    )
    case_status_enum = postgresql.ENUM(
        "submitted",
        "under_review",
        "approved",
        "declined",
        "awaiting_return",
        "return_received",
        "completed",
        "cancelled",
        name="store_order_case_status_enum",
        create_type=False,
    )
    case_resolution_enum = postgresql.ENUM(
        "full_refund_original_method",
        "partial_refund_original_method",
        "variant_exchange",
        "store_credit",
        "wallet_bubbles",
        "replacement_shipment",
        "no_refund",
        name="store_order_case_resolution_enum",
        create_type=False,
    )
    return_condition_enum = postgresql.ENUM(
        "pending",
        "restockable",
        "damaged",
        "hygiene_unsellable",
        name="store_return_inspection_condition_enum",
        create_type=False,
    )

    for enum_obj in (
        payment_status_enum,
        fulfillment_status_enum,
        case_type_enum,
        case_status_enum,
        case_resolution_enum,
        return_condition_enum,
    ):
        enum_obj.create(bind, checkfirst=True)

    # Order tender & separated lifecycle columns
    op.add_column(
        "store_orders",
        sa.Column("order_total_ngn", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "store_orders",
        sa.Column("payment_status", payment_status_enum, nullable=True),
    )
    op.add_column(
        "store_orders",
        sa.Column("fulfillment_status", fulfillment_status_enum, nullable=True),
    )
    op.add_column(
        "store_orders",
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_orders",
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "store_orders",
        sa.Column("refunded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_store_orders_payment_status", "store_orders", ["payment_status"]
    )
    op.create_index(
        "ix_store_orders_fulfillment_status", "store_orders", ["fulfillment_status"]
    )

    # Order item sourcing & policy snapshots
    op.add_column(
        "store_order_items",
        sa.Column(
            "sourcing_type",
            sa.String(24),
            nullable=False,
            server_default="stocked",
        ),
    )
    op.add_column(
        "store_order_items",
        sa.Column("net_line_total_ngn", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column(
        "store_order_items",
        sa.Column(
            "requires_size_chart_ack",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "store_order_items",
        sa.Column("supplier_committed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Store credit transaction type (debit vs restore)
    op.add_column(
        "store_credit_transactions",
        sa.Column(
            "transaction_type",
            sa.String(16),
            nullable=False,
            server_default="debit",
        ),
    )

    # Backfill order_total_ngn, payment_status, fulfillment_status, and preorder sourcing_type
    op.execute(
        """
        UPDATE store_orders
        SET order_total_ngn = GREATEST(0, subtotal_ngn - discount_amount_ngn + delivery_fee_ngn),
            payment_status = CASE status::text
                WHEN 'pending_payment' THEN 'unpaid'::store_order_payment_status_enum
                WHEN 'payment_failed' THEN 'failed'::store_order_payment_status_enum
                WHEN 'refunded' THEN 'refunded'::store_order_payment_status_enum
                WHEN 'cancelled' THEN CASE WHEN paid_at IS NOT NULL THEN 'paid'::store_order_payment_status_enum ELSE 'unpaid'::store_order_payment_status_enum END
                ELSE 'paid'::store_order_payment_status_enum
            END,
            fulfillment_status = CASE status::text
                WHEN 'pending_payment' THEN 'not_started'::store_order_fulfillment_status_enum
                WHEN 'payment_failed' THEN 'not_started'::store_order_fulfillment_status_enum
                WHEN 'paid' THEN 'awaiting_preparation'::store_order_fulfillment_status_enum
                WHEN 'processing' THEN 'processing'::store_order_fulfillment_status_enum
                WHEN 'ready_for_pickup' THEN 'ready_for_pickup'::store_order_fulfillment_status_enum
                WHEN 'picked_up' THEN 'picked_up'::store_order_fulfillment_status_enum
                WHEN 'shipped' THEN 'shipped'::store_order_fulfillment_status_enum
                WHEN 'delivered' THEN 'delivered'::store_order_fulfillment_status_enum
                WHEN 'cancelled' THEN 'cancelled'::store_order_fulfillment_status_enum
                WHEN 'refunded' THEN CASE WHEN fulfilled_at IS NOT NULL THEN 'returned'::store_order_fulfillment_status_enum ELSE 'cancelled'::store_order_fulfillment_status_enum END
                ELSE 'not_started'::store_order_fulfillment_status_enum
            END
        WHERE order_total_ngn IS NULL
        """
    )
    op.execute(
        """
        UPDATE store_order_items
        SET sourcing_type = 'preorder'
        WHERE is_preorder = true AND sourcing_type = 'stocked'
        """
    )

    # Post-purchase order cases
    op.create_table(
        "store_order_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("case_number", sa.String(28), nullable=False),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("store_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("member_auth_id", sa.String(255), nullable=True),
        sa.Column("case_type", case_type_enum, nullable=False),
        sa.Column(
            "status", case_status_enum, nullable=False, server_default="submitted"
        ),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("customer_notes", sa.Text(), nullable=True),
        sa.Column(
            "evidence_media_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("preferred_resolution", case_resolution_enum, nullable=True),
        sa.Column("approved_resolution", case_resolution_enum, nullable=True),
        sa.Column(
            "requested_variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("store_product_variants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "approved_refund_cash_ngn",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "approved_refund_bubbles",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "approved_refund_store_credit_ngn",
            sa.Numeric(12, 2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "delivery_fee_refunded",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("refund_reference", sa.String(64), nullable=True),
        sa.Column("refund_status", sa.String(32), nullable=True),
        sa.Column("recipient_bank_code", sa.String(32), nullable=True),
        sa.Column("recipient_bank_name", sa.String(128), nullable=True),
        sa.Column("recipient_account_number", sa.String(32), nullable=True),
        sa.Column("recipient_account_name", sa.String(255), nullable=True),
        sa.Column(
            "return_condition",
            return_condition_enum,
            nullable=False,
            server_default="pending",
        ),
        sa.Column("return_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("restocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_policy_override",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("admin_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_by_auth_id", sa.String(255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by_auth_id", sa.String(255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "approved_refund_cash_ngn >= 0", name="ck_order_case_cash_nonneg"
        ),
        sa.CheckConstraint(
            "approved_refund_bubbles >= 0", name="ck_order_case_bubbles_nonneg"
        ),
        sa.CheckConstraint(
            "approved_refund_store_credit_ngn >= 0", name="ck_order_case_credit_nonneg"
        ),
    )
    op.create_index(
        "ix_store_order_cases_case_number",
        "store_order_cases",
        ["case_number"],
        unique=True,
    )
    op.create_index("ix_store_order_cases_order_id", "store_order_cases", ["order_id"])
    op.create_index(
        "ix_store_order_cases_member_auth_id", "store_order_cases", ["member_auth_id"]
    )
    op.create_index(
        "ix_store_order_cases_case_type", "store_order_cases", ["case_type"]
    )
    op.create_index("ix_store_order_cases_status", "store_order_cases", ["status"])
    op.create_index(
        "ix_store_order_cases_refund_reference",
        "store_order_cases",
        ["refund_reference"],
    )
    op.create_index(
        "ix_store_order_cases_order_status",
        "store_order_cases",
        ["order_id", "status"],
    )

    op.create_table(
        "store_order_case_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("store_order_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("store_order_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "replacement_variant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("store_product_variants.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("quantity > 0", name="ck_order_case_item_positive_qty"),
    )
    op.create_index(
        "ix_store_order_case_items_case_id", "store_order_case_items", ["case_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_store_order_case_items_case_id", table_name="store_order_case_items")
    op.drop_table("store_order_case_items")
    op.drop_index("ix_store_order_cases_order_status", table_name="store_order_cases")
    op.drop_index("ix_store_order_cases_refund_reference", table_name="store_order_cases")
    op.drop_index("ix_store_order_cases_status", table_name="store_order_cases")
    op.drop_index("ix_store_order_cases_case_type", table_name="store_order_cases")
    op.drop_index("ix_store_order_cases_member_auth_id", table_name="store_order_cases")
    op.drop_index("ix_store_order_cases_order_id", table_name="store_order_cases")
    op.drop_index("ix_store_order_cases_case_number", table_name="store_order_cases")
    op.drop_table("store_order_cases")

    op.drop_column("store_credit_transactions", "transaction_type")
    op.drop_column("store_order_items", "supplier_committed_at")
    op.drop_column("store_order_items", "requires_size_chart_ack")
    op.drop_column("store_order_items", "net_line_total_ngn")
    op.drop_column("store_order_items", "sourcing_type")

    op.drop_index("ix_store_orders_fulfillment_status", table_name="store_orders")
    op.drop_index("ix_store_orders_payment_status", table_name="store_orders")
    op.drop_column("store_orders", "refunded_at")
    op.drop_column("store_orders", "delivered_at")
    op.drop_column("store_orders", "shipped_at")
    op.drop_column("store_orders", "fulfillment_status")
    op.drop_column("store_orders", "payment_status")
    op.drop_column("store_orders", "order_total_ngn")

    bind = op.get_bind()
    for enum_name in (
        "store_return_inspection_condition_enum",
        "store_order_case_resolution_enum",
        "store_order_case_status_enum",
        "store_order_case_type_enum",
        "store_order_fulfillment_status_enum",
        "store_order_payment_status_enum",
    ):
        postgresql.ENUM(name=enum_name).drop(bind, checkfirst=True)
