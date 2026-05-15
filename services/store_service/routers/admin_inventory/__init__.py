"""Admin inventory + orders router package.

Split from `routers/admin_inventory.py` (772 lines) per CONVENTIONS §12.

Submodules:
  _helpers.py   inventory release on cancel/refund + order status transition
                state machine + admin-order eager loader
  inventory.py  /inventory list, low-stock, adjust
  orders.py     /orders list, count, get, status-change, update, mark-paid,
                refund
"""

from fastapi import APIRouter

from . import inventory as _inventory
from . import orders as _orders

router = APIRouter(tags=["admin-store"])
router.include_router(_inventory.router)
router.include_router(_orders.router)

__all__ = ["router"]
