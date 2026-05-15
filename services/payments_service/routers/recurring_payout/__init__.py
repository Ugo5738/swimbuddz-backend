"""Recurring payouts + make-up obligations router package.

Original 912-line `routers/recurring_payout.py` exposed four prefixed
routers; this package preserves all four under their original public
names (`admin_router`, `makeups_admin_router`, `makeups_coach_router`,
`coach_earnings_router`). The four import lines in `app/main.py` are
unchanged.

Submodules:
  - _helpers.py         _fetch_cohort_snapshot, _resolve_coach_member_id
  - recurring_config.py admin: create / list / get / update / preview / run-now
                        for RecurringPayoutConfig
  - makeups_admin.py    admin: list / schedule / cancel make-up obligations
  - makeups_coach.py    coach: list own obligations + schedule against own sessions
  - coach_earnings.py   coach earnings summary (forward preview + lifetime totals)
"""

from fastapi import APIRouter

from . import coach_earnings as _coach_earnings
from . import makeups_admin as _makeups_admin
from . import makeups_coach as _makeups_coach
from . import recurring_config as _recurring_config

admin_router = APIRouter(
    prefix="/admin/recurring-payouts", tags=["admin-recurring-payouts"]
)
makeups_admin_router = APIRouter(
    prefix="/admin/cohort-makeups", tags=["admin-cohort-makeups"]
)
makeups_coach_router = APIRouter(
    prefix="/coach/me/cohort-makeups", tags=["coach-cohort-makeups"]
)
coach_earnings_router = APIRouter(prefix="/coach/me/earnings", tags=["coach-earnings"])

admin_router.include_router(_recurring_config.router)
makeups_admin_router.include_router(_makeups_admin.router)
makeups_coach_router.include_router(_makeups_coach.router)
coach_earnings_router.include_router(_coach_earnings.router)

__all__ = [
    "admin_router",
    "makeups_admin_router",
    "makeups_coach_router",
    "coach_earnings_router",
]
