"""Scoring router package.

Split from `routers/scoring.py` (707 lines) per CONVENTIONS §12.

Submodules:
  lookup.py     /scoring/calculate (preview) + dimension labels
  complexity.py /cohorts/{id}/complexity-score CRUD + review mark
  matching.py   /cohorts/{id}/eligible-coaches
  ai.py         /cohorts/{id}/ai-score + /cohorts/{id}/ai-suggest-coach
"""

from fastapi import APIRouter

from . import ai as _ai
from . import complexity as _complexity
from . import lookup as _lookup
from . import matching as _matching

router = APIRouter(tags=["academy"])
router.include_router(_lookup.router)
router.include_router(_complexity.router)
router.include_router(_matching.router)
router.include_router(_ai.router)

__all__ = ["router"]
