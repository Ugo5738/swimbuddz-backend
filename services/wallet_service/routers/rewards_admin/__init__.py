"""Admin rewards router package.

Split from `routers/rewards_admin.py` (702 lines) per CONVENTIONS §12.
Aggregator owns the /admin/wallet/rewards prefix and the require_admin
dependency. Sub-routers are bare.

Submodules:
  rules.py     /rules CRUD (list, create, get, update)
  events.py    /events list, /events/failed, POST /events/submit
  alerts.py    /alerts list, /alerts/summary, /alerts/{id} GET + PATCH
  reports.py   /stats + /analytics
"""

from fastapi import APIRouter, Depends
from libs.auth.dependencies import require_admin

from . import alerts as _alerts
from . import events as _events
from . import reports as _reports
from . import rules as _rules

router = APIRouter(
    prefix="/admin/wallet/rewards",
    tags=["admin-rewards"],
    dependencies=[Depends(require_admin)],
)
router.include_router(_rules.router)
router.include_router(_events.router)
router.include_router(_alerts.router)
router.include_router(_reports.router)

__all__ = ["router"]
