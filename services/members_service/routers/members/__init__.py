"""Members router package.

Split from `routers/members.py` (716 lines) per CONVENTIONS §12.

Route order matters: static-literal routes (/me, /public, /bulk-basic,
/stats, /by-auth/{id}, /public/{id}, /directory) must register BEFORE
the 1-seg catch-all /{member_id}. The include order below preserves
that invariant — `admin.py` (which owns /{id}) is included last.

Submodules:
  me.py      /me, /me/badges, PATCH /me
  public.py  /public, /directory, /public/{id}
  bulk.py    /bulk-basic
  admin.py   POST /, GET /, /stats, /by-auth/{id}, /{id} CRUD
"""

from fastapi import APIRouter

from . import admin as _admin
from . import bulk as _bulk
from . import me as _me
from . import public as _public

router = APIRouter(prefix="/members", tags=["members"])
router.include_router(_me.router)
router.include_router(_public.router)
router.include_router(_bulk.router)
router.include_router(_admin.router)

__all__ = ["router"]
