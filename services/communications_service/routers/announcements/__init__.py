"""Announcements router package.

Split from 712-line `routers/announcements.py` per CONVENTIONS §12.
Preserves both exports:
  * `router` — prefix /announcements, tags=announcements
  * `admin_router` — prefix /admin, tags=admin (only the admin-delete
    member-comments endpoint lives here)

Submodules:
  _helpers.py  audience, expiry, notification flag, email dispatch helpers
  crud.py      /announcements list, stats, unread-count, get, CRUD
  reads.py     /announcements/{id}/read + read-status + read-stats
  comments.py  /announcements/{id}/comments POST + GET
  admin.py     DELETE /admin/members/{member_id} (bulk comment purge)
"""

from fastapi import APIRouter

from . import admin as _admin
from . import comments as _comments
from . import crud as _crud
from . import reads as _reads

router = APIRouter(prefix="/announcements", tags=["announcements"])
router.include_router(_crud.router)
router.include_router(_reads.router)
router.include_router(_comments.router)

admin_router = APIRouter(prefix="/admin", tags=["admin"])
admin_router.include_router(_admin.router)

__all__ = ["router", "admin_router"]
