"""Volunteer member-facing router package.

Original 941-line `routers/member.py` was split into 8 focused
submodules + a small aggregator (see docs/CONVENTIONS.md §12.1).
Public import `from services.volunteer_service.routers.member import
router as volunteer_router` resolves to this package's `__init__.py`
and keeps working unchanged.

**Route-ordering invariant:** `/opportunities/upcoming` (static) must
register before `/opportunities/{opp_id}` (catch-all). Both live in
`opportunities.py` in that order, so include order between submodules
doesn't matter.

Submodules:
  - _helpers.py       _enrich_opportunity helper + QR check-in window constants
  - roles.py          GET /roles, /roles/{id}
  - spotlight.py      GET /spotlight (public featured volunteer + stats)
  - profile.py        GET/POST/PATCH /profile/me
  - opportunities.py  GET /opportunities, /opportunities/upcoming, /opportunities/{id}
  - slots.py          POST/DELETE /opportunities/{id}/claim
  - hours.py          GET /hours/me, /hours/me/summary, /hours/leaderboard
  - rewards.py        GET /rewards/me, POST /rewards/{id}/redeem
  - qr_checkin.py     POST /qr-checkin
"""

from fastapi import APIRouter

from . import hours as _hours
from . import opportunities as _opportunities
from . import profile as _profile
from . import qr_checkin as _qr_checkin
from . import rewards as _rewards
from . import roles as _roles
from . import slots as _slots
from . import spotlight as _spotlight

router = APIRouter(prefix="/volunteers", tags=["volunteers"])

router.include_router(_roles.router)
router.include_router(_spotlight.router)
router.include_router(_profile.router)
router.include_router(_opportunities.router)
router.include_router(_slots.router)
router.include_router(_hours.router)
router.include_router(_rewards.router)
router.include_router(_qr_checkin.router)

__all__ = ["router"]
