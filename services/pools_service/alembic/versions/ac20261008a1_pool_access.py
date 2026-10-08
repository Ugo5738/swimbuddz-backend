"""Pool Access published offers, provisional bookings, admissions and reconciliation.

Revision ID: ac20261008a1
Revises: d4e65b190ac7
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
revision = "ac20261008a1"
down_revision = "d4e65b190ac7"
branch_labels = None
depends_on = None

def upgrade():
    op.create_table("pool_access_offers",
      sa.Column("id",sa.UUID(),primary_key=True),
      sa.Column("pool_id",sa.UUID(),sa.ForeignKey("pools.id",ondelete="RESTRICT"),nullable=False,index=True),
      sa.Column("title",sa.String(160),nullable=False),
      sa.Column("starts_at",sa.DateTime(timezone=True),nullable=False),
      sa.Column("ends_at",sa.DateTime(timezone=True),nullable=False),
      sa.Column("capacity",sa.Integer(),nullable=False),
      sa.Column("selling_price_kobo",sa.Integer(),nullable=False),
      sa.Column("negotiated_cost_kobo",sa.Integer(),nullable=False),
      sa.Column("cost_basis",sa.String(16),nullable=False),
      sa.Column("currency",sa.String(3),nullable=False),
      sa.Column("status",sa.String(16),nullable=False),
      sa.Column("public_booking_enabled",sa.Boolean(),nullable=False),
      sa.Column("self_directed_permitted",sa.Boolean(),nullable=False),
      sa.Column("admissions_require_lifeguard",sa.Boolean(),nullable=False),
      sa.Column("amenities",postgresql.JSONB(),nullable=False),
      sa.Column("access_rules",sa.Text(),nullable=False),
      sa.Column("cancellation_policy",sa.Text(),nullable=False),
      sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
      sa.CheckConstraint("capacity > 0",name="ck_pool_access_offer_capacity"),
      sa.CheckConstraint("selling_price_kobo > 0",name="ck_pool_access_offer_price"),
      sa.CheckConstraint("negotiated_cost_kobo >= 0",name="ck_pool_access_offer_cost"),
      sa.CheckConstraint("ends_at > starts_at",name="ck_pool_access_offer_window"),
      sa.CheckConstraint("cost_basis IN ('per_person', 'per_group')",name="ck_pool_access_cost_basis"),
      sa.CheckConstraint("status IN ('draft', 'published', 'paused')",name="ck_pool_access_offer_status"))
    op.create_table("pool_access_bookings",
      sa.Column("id",sa.UUID(),primary_key=True),
      sa.Column("offer_id",sa.UUID(),sa.ForeignKey("pool_access_offers.id",ondelete="RESTRICT"),nullable=False,index=True),
      sa.Column("buyer_auth_id",sa.String(255),nullable=True,index=True),
      sa.Column("buyer_email",sa.String(255),nullable=False),
      sa.Column("idempotency_key",sa.String(100),nullable=False),
      sa.Column("headcount",sa.Integer(),nullable=False),
      sa.Column("selling_total_kobo",sa.Integer(),nullable=False),
      sa.Column("negotiated_cost_kobo",sa.Integer(),nullable=False),
      sa.Column("cost_basis",sa.String(16),nullable=False),
      sa.Column("currency",sa.String(3),nullable=False),
      sa.Column("status",sa.String(20),nullable=False),
      sa.Column("payment_reference",sa.String(160),nullable=True,unique=True),
      sa.Column("confirmed_at",sa.DateTime(timezone=True),nullable=True),
      sa.Column("hold_expires_at",sa.DateTime(timezone=True),nullable=False),
      sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
      sa.UniqueConstraint("buyer_auth_id","idempotency_key",name="uq_pool_access_buyer_idempotency"),
      sa.CheckConstraint("headcount > 0",name="ck_pool_access_booking_headcount"),
      sa.CheckConstraint("status IN ('pending_payment','confirmed','cancelled')",name="ck_pool_access_booking_status"))
    op.create_table("pool_access_admissions",
      sa.Column("id",sa.UUID(),primary_key=True),
      sa.Column("booking_id",sa.UUID(),sa.ForeignKey("pool_access_bookings.id",ondelete="RESTRICT"),nullable=False,index=True),
      sa.Column("ordinal",sa.Integer(),nullable=False),
      sa.Column("guest_name",sa.String(150),nullable=False),
      sa.Column("checked_in_at",sa.DateTime(timezone=True),nullable=True),
      sa.Column("checked_in_by",sa.String(255),nullable=True),
      sa.UniqueConstraint("booking_id","ordinal",name="uq_pool_access_admission_ordinal"))
    op.create_table("pool_access_reconciliations",
      sa.Column("id",sa.UUID(),primary_key=True),
      sa.Column("booking_id",sa.UUID(),sa.ForeignKey("pool_access_bookings.id",ondelete="RESTRICT"),nullable=False,unique=True),
      sa.Column("verified_admissions",sa.Integer(),nullable=False),
      sa.Column("payable_kobo",sa.Integer(),nullable=False),
      sa.Column("currency",sa.String(3),nullable=False),
      sa.Column("payment_reference",sa.String(160),nullable=False),
      sa.Column("reconciled_by",sa.String(255),nullable=False),
      sa.Column("reconciled_at",sa.DateTime(timezone=True),nullable=False))

def downgrade():
    for table in ("pool_access_reconciliations","pool_access_admissions","pool_access_bookings","pool_access_offers"):
        op.drop_table(table)
