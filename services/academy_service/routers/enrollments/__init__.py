"""Enrollment router package.

Original 1534-line `routers/enrollments.py` was split into 6 focused
submodules + a small aggregator (see docs/CONVENTIONS.md §12.1). Public
import `from services.academy_service.routers.enrollments import router
as enrollments_router` resolves to this package's `__init__.py` and
keeps working unchanged.

Submodules:
  - _helpers.py        private helpers for the withdrawal flow
  - admin_crud.py      POST /enrollments, GET /enrollments,
                       GET/PATCH /enrollments/{id}
  - self_enroll.py     POST /enrollments/me (member self-enrollment)
  - me.py              GET /my-enrollments, /my-enrollments/{id}/waitlist-position,
                       /my-enrollments/{id}, POST /my-enrollments/{id}/withdraw
  - admin_payments.py  POST /admin/enrollments/{id}/mark-paid + /dropout-action
  - by_cohort.py       GET /cohorts/{id}/enrollments, /cohorts/{id}/analytics
  - onboarding.py      GET /my-enrollments/{id}/onboarding

Note: the test at tests/integration/test_academy_public.py patches
`enrollments.get_member_by_id` — that import lives in `admin_payments.py`
now, so the test patch path is updated to
`services.academy_service.routers.enrollments.admin_payments.get_member_by_id`.
"""

from fastapi import APIRouter

from . import admin_crud as _admin_crud
from . import admin_payments as _admin_payments
from . import by_cohort as _by_cohort
from . import me as _me
from . import onboarding as _onboarding
from . import self_enroll as _self_enroll

router = APIRouter(tags=["academy"])

router.include_router(_admin_crud.router)
router.include_router(_self_enroll.router)
router.include_router(_me.router)
router.include_router(_admin_payments.router)
router.include_router(_by_cohort.router)
router.include_router(_onboarding.router)

__all__ = ["router"]
