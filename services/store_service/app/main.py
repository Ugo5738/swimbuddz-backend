"""FastAPI application for the Store Service."""

from fastapi import FastAPI
from services.store_service.routers import (
    admin_catalog_router,
    admin_credits_router,
    admin_inventory_router,
    cart_router,
    catalog_router,
    orders_router,
)


def create_app() -> FastAPI:
    """Create and configure the Store Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Store Service",
        version="0.1.0",
        description="E-commerce service for SwimBuddz - product catalog, cart, checkout, orders.",
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "ok", "service": "store"}

    # Public store routes (catalog, cart, checkout, orders)
    app.include_router(catalog_router, prefix="/store")
    app.include_router(cart_router, prefix="/store")
    app.include_router(orders_router, prefix="/store")

    # Admin routes (product management, inventory, order management)
    app.include_router(admin_catalog_router, prefix="/admin/store")
    app.include_router(admin_inventory_router, prefix="/admin/store")
    app.include_router(admin_credits_router, prefix="/admin/store")

    return app


app = create_app()
