"""Members service routers - backwards compatibility re-exports.

All router logic has been decomposed into smaller, focused files in the routers/ directory.
This file re-exports the routers for backwards compatibility with existing imports.
"""

from services.members_service.routers import (
    registration_router,
    members_router,
    coaches_router,
    admin_router,
)

# Re-export with original names for backwards compatibility
router = members_router

__all__ = [
    "router",
    "registration_router",
    "admin_router",
    "coaches_router",
]
