"""Wallet service routers."""

from services.wallet_service.routers.admin import router as admin_router
from services.wallet_service.routers.internal import router as internal_router
from services.wallet_service.routers.member import router as wallet_router

__all__ = [
    "admin_router",
    "internal_router",
    "wallet_router",
]
