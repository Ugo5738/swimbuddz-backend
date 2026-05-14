"""Admin store catalog router package.

Original 874-line `routers/admin_catalog.py` was split into per-domain
submodules (see docs/CONVENTIONS.md §12). Mounted via
``app.include_router(admin_catalog_router, prefix="/admin/store")`` in
`app/main.py` — no prefix is set on the routers themselves.

Submodules:
  - _constants.py  CATEGORY_SKU_CODES used by variant SKU generation
  - categories.py  CRUD for categories
  - products.py    CRUD for products (list/create/get/update/archive)
  - variants.py    variant create/update/delete (with SKU auto-gen)
  - images.py      product image add/update/delete
  - videos.py      product video add/delete
  - collections.py collections + collection-product membership
"""

from fastapi import APIRouter

from . import categories as _categories
from . import collections as _collections
from . import images as _images
from . import products as _products
from . import variants as _variants
from . import videos as _videos

router = APIRouter(tags=["admin-store"])
router.include_router(_categories.router)
router.include_router(_products.router)
router.include_router(_variants.router)
router.include_router(_images.router)
router.include_router(_videos.router)
router.include_router(_collections.router)

__all__ = ["router"]
