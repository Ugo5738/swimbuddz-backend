"""Challenges router package.

Original 2313-line `routers/challenges.py` was split into 6 focused
submodules + a small aggregator (see docs/CONVENTIONS.md §12.1). The
public surface — `from services.members_service.routers.challenges
import challenge_router, volunteer_router` (used by `routers/__init__.py`)
— is preserved unchanged.

**Route-ordering invariant** within `/challenges`:
  - `/public/*`             (2-segment literal) — independent
  - `/series/list`          (2-segment) MUST register before
    `/{challenge_id}/completions` (2-segment catch-all)
  - `/submissions/mine`,
    `/submissions/pending`,
    `/submissions/list`     (2-segment literal)
  - `/{challenge_id}` (1-segment catch-all) is registered with the rest
    of the club-CRUD sub-router; submissions and completions sub-routers
    come after it but rely on different methods or path shapes.

Submodules:
  - _helpers.py             21 helpers + CHALLENGES_CALLING_SERVICE
  - volunteer_legacy.py     legacy volunteer roles & interest (admin)
  - public_challenges.py    GET /public/all, /public/{id}
  - club_challenges_crud.py GET /, /series/list, /{id}; POST/PATCH/DELETE /{id}
  - submissions.py          GET /submissions/mine, POST /{id}/submissions
  - admin_submissions.py    GET /submissions/{pending,list};
                            PATCH /submissions/{id};
                            POST /submissions/{id}/{revoke,mark-winner}
  - completions.py          POST /completions, GET /{id}/completions
"""

from fastapi import APIRouter

from . import admin_submissions as _admin_submissions
from . import club_challenges_crud as _club_challenges_crud
from . import completions as _completions
from . import public_challenges as _public_challenges
from . import submissions as _submissions
from . import volunteer_legacy as _volunteer_legacy

# Legacy volunteer-role surface (gated to admin)
volunteer_router = APIRouter(prefix="/volunteers", tags=["volunteers"])
volunteer_router.include_router(_volunteer_legacy.router)

# Club challenges + submissions
challenge_router = APIRouter(prefix="/challenges", tags=["challenges"])
# Order: public + series/list-style static paths first so they don't get
# captured by the /{challenge_id} catch-all in club_challenges_crud.
challenge_router.include_router(_public_challenges.router)
challenge_router.include_router(_club_challenges_crud.router)
challenge_router.include_router(_submissions.router)
challenge_router.include_router(_admin_submissions.router)
challenge_router.include_router(_completions.router)

__all__ = ["challenge_router", "volunteer_router"]
