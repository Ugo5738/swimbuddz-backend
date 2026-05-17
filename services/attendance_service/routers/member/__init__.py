"""Aggregator for the attendance member-facing routes.

Original 851-line module was split into per-area submodules (see
docs/CONVENTIONS.md §12):

  - _shared.py        — auth helpers + tier-based access control
  - _milestones.py    — best-effort milestone/streak emitter (private)
  - sign_in.py        — POST /sessions/{id}/sign-in + public variant
  - book.py           — POST /sessions/{id}/book, POST /bookings/{id}/cancel (A1 Phase 3.3)
  - coach_mark.py     — POST /sessions/{id}/coach-mark
  - lists.py          — GET /sessions/{id}/attendance, /cohorts/{id}/attendance/summary, /me
  - admin.py          — GET /sessions/{id}/pool-list, DELETE /admin/members/{id}

This module exposes a single `router` that includes each sub-router, so
``from services.attendance_service.routers.member import router as attendance_router``
in `app/main.py` continues to work unchanged.

We also re-export `get_current_member` here because `app/tests/test_api.py`
imports it directly from this module.
"""

from fastapi import APIRouter

from . import admin as _admin
from . import book as _book
from . import coach_mark as _coach_mark
from . import lists as _lists
from . import sign_in as _sign_in
from ._shared import get_current_member  # re-exported for tests

router = APIRouter(tags=["attendance"])

# Order doesn't matter here — every sub-router uses a distinct (method, path)
# combination, so FastAPI can resolve them unambiguously.
router.include_router(_sign_in.router)
router.include_router(_book.router)
router.include_router(_coach_mark.router)
router.include_router(_lists.router)
router.include_router(_admin.router)

__all__ = ["router", "get_current_member"]
