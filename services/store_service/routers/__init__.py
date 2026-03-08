"""Store service routers package."""

from services.store_service.routers.admin_catalog import router as admin_catalog_router
from services.store_service.routers.admin_credits import router as admin_credits_router
from services.store_service.routers.admin_inventory import (
    router as admin_inventory_router,
)
from services.store_service.routers.admin_maintenance import (
    router as admin_maintenance_router,
)
from services.store_service.routers.admin_payouts import router as admin_payouts_router
from services.store_service.routers.admin_reports import router as admin_reports_router
from services.store_service.routers.admin_suppliers import (
    router as admin_suppliers_router,
)
from services.store_service.routers.cart import router as cart_router
from services.store_service.routers.catalog import router as catalog_router
from services.store_service.routers.checkout import router as checkout_router
from services.store_service.routers.orders import router as orders_router

__all__ = [
    "admin_catalog_router",
    "admin_credits_router",
    "admin_inventory_router",
    "admin_maintenance_router",
    "admin_payouts_router",
    "admin_reports_router",
    "admin_suppliers_router",
    "cart_router",
    "catalog_router",
    "checkout_router",
    "orders_router",
]
