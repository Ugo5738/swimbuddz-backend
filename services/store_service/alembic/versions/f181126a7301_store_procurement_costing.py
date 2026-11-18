"""Add strictly separate Store operational procurement and valuation subledger.

Revision ID: f181126a7301
Revises: 2de3f310326d

Schema-only; no historical stock, order or money records are fabricated.
"""
from alembic import op
from services.store_service.models.procurement import (
    StoreProcurementGrant, StorePurchaseOrder, StorePurchaseOrderLine,
    StoreGoodsReceipt, StoreGoodsReceiptLine, StoreInventoryValuation,
    StoreCostEvent, StoreSupplierInvoice, StoreTradeReceivable,
    StoreOpeningReconciliation,
)

revision = "f181126a7301"
down_revision = "2de3f310326d"
branch_labels = None
depends_on = None

_TABLES = (
    StoreProcurementGrant, StorePurchaseOrder, StorePurchaseOrderLine,
    StoreGoodsReceipt, StoreGoodsReceiptLine, StoreInventoryValuation,
    StoreCostEvent, StoreSupplierInvoice, StoreTradeReceivable,
    StoreOpeningReconciliation,
)

def upgrade():
    # SQLAlchemy emits the exact model constraints and indexes (including
    # existing relationships). Tables are created in dependency order.
    bind = op.get_bind()
    for model in _TABLES:
        model.__table__.create(bind, checkfirst=False)

def downgrade():
    bind = op.get_bind()
    for model in reversed(_TABLES):
        model.__table__.drop(bind, checkfirst=False)
