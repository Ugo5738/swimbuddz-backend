"""Routers package."""

from services.payments_service.routers.discounts import router as discounts_router
from services.payments_service.routers.intents import router as intents_router
from services.payments_service.routers.internal import router as internal_router
from services.payments_service.routers.manual import router as manual_router
from services.payments_service.routers.webhooks import router as webhooks_router

__all__ = [
    "discounts_router",
    "intents_router",
    "internal_router",
    "manual_router",
    "webhooks_router",
]
