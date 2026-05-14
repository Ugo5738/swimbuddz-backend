"""Internal service-to-service router package for members-service.

Original 843-line `routers/internal.py` was split into per-area submodules
(see docs/CONVENTIONS.md §12). These endpoints are authenticated with
service_role JWT only and are NOT exposed through the gateway — only
other backend services call them directly via Docker network.

**Route-ordering invariant:** several static-path routes
(`/active`, `/search`, `/approved-list`, `/birthdays-today`, `/admins`,
`/joined-tier`, `/coaches/eligible`) MUST be registered before the
catch-all `/{member_id}` route, otherwise FastAPI captures the literal
segment as a UUID parameter. The sub-router include order below preserves
that invariant.

Submodules:
  - _schemas.py    Pydantic response shapes
  - _helpers.py    helpers + module constants (_age_on, _LAGOS_TZ, …)
  - lookups.py     /by-auth/{auth_id}, /active, /search, /approved-list
  - birthdays.py   /birthdays-today, /admins
  - flywheel.py    /joined-tier
  - coach.py       /coaches/eligible, /coaches/{member_id}/profile,
                   /coaches/{member_id}/readiness, /{member_id}/bank-account
  - membership.py  /{member_id}/membership, /{member_id}/tier-history,
                   /{member_id}, /bulk
"""

from fastapi import APIRouter

from . import birthdays as _birthdays
from . import coach as _coach
from . import flywheel as _flywheel
from . import lookups as _lookups
from . import membership as _membership

router = APIRouter(prefix="/internal/members", tags=["internal"])

# Order matters — static paths first so FastAPI doesn't capture literal
# segments as UUIDs against the /{member_id} catch-all in membership.py.
router.include_router(_lookups.router)
router.include_router(_birthdays.router)
router.include_router(_flywheel.router)
router.include_router(_coach.router)
router.include_router(_membership.router)

__all__ = ["router"]
