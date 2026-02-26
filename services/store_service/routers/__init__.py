"""Store service routers package."""

from services.store_service.routers.admin_catalog import router as admin_catalog_router
from services.store_service.routers.admin_credits import router as admin_credits_router
from services.store_service.routers.admin_inventory import (
    router as admin_inventory_router,
)
from services.store_service.routers.cart import router as cart_router
from services.store_service.routers.catalog import router as catalog_router
from services.store_service.routers.orders import router as orders_router

__all__ = [
    "admin_catalog_router",
    "admin_credits_router",
    "admin_inventory_router",
    "cart_router",
    "catalog_router",
    "orders_router",
]
