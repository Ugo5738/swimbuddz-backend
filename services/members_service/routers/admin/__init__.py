"""Admin members router package.

Original 804-line `routers/admin.py` was split into per-domain submodules
(see docs/CONVENTIONS.md §12). The package exposes a single aggregator
`router` so `from services.members_service.routers.admin import router as
admin_router` in `app/main.py` keeps working unchanged.

Submodules:
  - _shared.py    helper: _apply_wallet_paid_activation_side_effects
  - approval.py   pending listing, by-email lookup, approve/reject/upgrade
  - community.py  community tier activate / extend
  - club.py       club tier activate / extend
  - academy.py    academy tier activate / expire
  - patch.py      partial-update membership patch
"""

from fastapi import APIRouter

from . import academy as _academy
from . import approval as _approval
from . import club as _club
from . import community as _community
from . import patch as _patch

router = APIRouter(prefix="/admin/members", tags=["admin-members"])
router.include_router(_approval.router)
router.include_router(_community.router)
router.include_router(_club.router)
router.include_router(_academy.router)
router.include_router(_patch.router)

__all__ = ["router"]
