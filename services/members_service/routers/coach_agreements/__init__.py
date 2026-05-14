"""Coach agreement & handbook router package.

Original 814-line `routers/coach_agreements.py` was split into per-area
submodules (see docs/CONVENTIONS.md §12). Exposes two routers — `router`
(coach-facing, `/coaches`) and `admin_router` (admin-only,
`/admin/coaches`) — so existing imports in `routers/__init__.py` keep
working unchanged.

Submodules:
  - _shared.py             helper utilities (placeholder rendering, hashing)
  - public_agreement.py    coach: get / status / sign / history
  - public_handbook.py     coach: get current handbook / get version
  - admin_agreement.py     admin: list / create / get agreement versions
  - admin_handbook.py      admin: list / create handbook versions
"""

from fastapi import APIRouter

from . import admin_agreement as _admin_agreement
from . import admin_handbook as _admin_handbook
from . import public_agreement as _public_agreement
from . import public_handbook as _public_handbook

router = APIRouter(prefix="/coaches", tags=["coaches"])
admin_router = APIRouter(prefix="/admin/coaches", tags=["admin-coaches"])

router.include_router(_public_agreement.router)
router.include_router(_public_handbook.router)
admin_router.include_router(_admin_agreement.router)
admin_router.include_router(_admin_handbook.router)

__all__ = ["router", "admin_router"]
