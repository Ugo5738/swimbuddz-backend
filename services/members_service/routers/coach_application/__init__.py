"""Coach application + onboarding router package.

Original 865-line `routers/coach_application.py` was split into per-area
submodules (see docs/CONVENTIONS.md §12). Exposes two routers — `router`
(coach-facing, `/coaches`) and `admin_router` (admin-only,
`/admin/coaches`) — so existing imports in `routers/__init__.py` keep
working unchanged.

Submodules:
  - _shared.py  helpers: profile lookups, wallet auto-provision, response builder
  - public.py   coach: apply, get/update/preferences/onboarding self
  - admin.py    admin: list / review / approve / reject / request-info / delete
"""

from fastapi import APIRouter

from . import admin as _admin
from . import public as _public

router = APIRouter(prefix="/coaches", tags=["coaches"])
admin_router = APIRouter(prefix="/admin/coaches", tags=["admin-coaches"])

router.include_router(_public.router)
admin_router.include_router(_admin.router)

__all__ = ["router", "admin_router"]
