"""Routers package."""

from services.payments_service.routers.charges import router as charges_router
from services.payments_service.routers.discounts import router as discounts_router
from services.payments_service.routers.intents import router as intents_router
from services.payments_service.routers.internal import router as internal_router
from services.payments_service.routers.manual import router as manual_router
from services.payments_service.routers.refunds import (
    admin_router as refunds_admin_router,
    internal_router as refunds_internal_router,
    member_router as refunds_member_router,
)
from services.payments_service.routers.webhooks import router as webhooks_router

__all__ = [
    "charges_router",
    "discounts_router",
    "intents_router",
    "internal_router",
    "manual_router",
    "refunds_admin_router",
    "refunds_internal_router",
    "refunds_member_router",
    "webhooks_router",
]
