"""Admin volunteer router package.

Original 1064-line `routers/admin.py` was split into 8 focused
submodules + a small aggregator (see docs/CONVENTIONS.md §12.1). Public
import `from services.volunteer_service.routers.admin import router as
admin_router` resolves through the package's __init__.py — unchanged.

Submodules:
  - _helpers.py       _is_peer_coaching, _emit_volunteer_reward,
                      _enrich_opportunity, _enrich_slot, _auto_checkout_if_past
  - roles.py          volunteer role CRUD
  - profiles.py       profile listing/lookup/update + spotlight feature/unfeature
  - opportunities.py  opportunity CRUD + bulk-create + publish
  - slots.py          slot listing / update / check-in/out / no-show / bulk-complete
  - hours.py          manual hours entry
  - rewards.py        grant + list rewards
  - dashboard.py      dashboard summary + reliability report
"""

from fastapi import APIRouter

from . import dashboard as _dashboard
from . import hours as _hours
from . import opportunities as _opportunities
from . import profiles as _profiles
from . import rewards as _rewards
from . import roles as _roles
from . import slots as _slots
from . import templates as _templates

router = APIRouter(prefix="/admin/volunteers", tags=["admin-volunteers"])

router.include_router(_roles.router)
router.include_router(_profiles.router)
router.include_router(_opportunities.router)
router.include_router(_slots.router)
router.include_router(_hours.router)
router.include_router(_rewards.router)
router.include_router(_dashboard.router)
router.include_router(_templates.router)

__all__ = ["router"]
