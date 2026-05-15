"""Curriculum router package — skills + program curriculum + weeks + lessons.

Split from the 792-line `routers/curriculum.py` per docs/CONVENTIONS.md §12.
Aggregator owns a single APIRouter, sub-routers are bare so the routes
keep their original paths.

Submodules:
  _helpers.py    sync_curriculum_json + the 3 get_program_id_* lookups
  skills.py      GET/POST/PUT/DELETE /skills (skill library)
  curricula.py   GET/POST /programs/{id}/curriculum
  weeks.py       POST /curricula/{id}/weeks; PUT/DELETE
                 /curriculum-weeks/{id}; PUT
                 /curricula/{id}/weeks/reorder
  lessons.py     POST /curriculum-weeks/{id}/lessons; PUT/DELETE
                 /curriculum-lessons/{id}; PUT
                 /curriculum-weeks/{id}/lessons/reorder
"""

from fastapi import APIRouter

from . import curricula as _curricula
from . import lessons as _lessons
from . import skills as _skills
from . import weeks as _weeks

router = APIRouter(tags=["curriculum"])
router.include_router(_skills.router)
router.include_router(_curricula.router)
router.include_router(_weeks.router)
router.include_router(_lessons.router)

__all__ = ["router"]
