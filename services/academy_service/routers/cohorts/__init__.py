"""Cohort router package.

Original 929-line `routers/cohorts.py` was split into 5 focused
submodules + a small aggregator (see docs/CONVENTIONS.md §12.1).
Existing import `from services.academy_service.routers.cohorts import
router as cohorts_router` resolves to this package's `__init__.py` and
keeps working unchanged.

**Route-ordering invariant:** several static-path GET routes
(`/cohorts/open`, `/cohorts/enrollable`, `/cohorts/by-coach/{id}`,
`/cohorts/coach/me`) MUST be registered before `/cohorts/{cohort_id}`,
otherwise FastAPI captures the literal segment as a UUID. The include
order below preserves that invariant.

Submodules:
  - lists.py             /cohorts, /cohorts/open, /cohorts/enrollable,
                         /cohorts/by-coach/{id}, /cohorts/coach/me
  - crud.py              POST /cohorts, PUT/DELETE /cohorts/{id},
                         GET /cohorts/{id}
  - enrollment_stats.py  GET /cohorts/{id}/enrollment-stats, /students
  - timeline_shift.py    /cohorts/{id}/timeline-shifts (preview / apply / log list)
  - resources.py         GET /cohorts/{id}/resources
"""

from fastapi import APIRouter

from . import crud as _crud
from . import enrollment_stats as _enrollment_stats
from . import lists as _lists
from . import resources as _resources
from . import timeline_shift as _timeline_shift

router = APIRouter(tags=["academy"])

# Order matters — lists.router exposes the static-prefix /cohorts/open,
# /cohorts/enrollable, /cohorts/by-coach/{id}, /cohorts/coach/me. They
# must be registered before crud.router's /cohorts/{cohort_id} catch-all.
router.include_router(_lists.router)
router.include_router(_crud.router)
router.include_router(_enrollment_stats.router)
router.include_router(_timeline_shift.router)
router.include_router(_resources.router)

__all__ = ["router"]
