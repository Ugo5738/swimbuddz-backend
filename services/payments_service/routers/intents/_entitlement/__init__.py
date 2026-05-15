"""Payment entitlement application.

Replaces the monolithic `intents/_entitlement.py` (1065 lines) with one
dispatcher + one handler per PaymentPurpose value, per docs/CONVENTIONS.md §12.

Public surface preserved (re-exported by `intents/__init__.py` for the
retry worker, route modules, and tests):
  - `_apply_entitlement`
  - `_apply_entitlement_with_tracking`
  - `_mark_paid_and_apply`
"""

from ._dispatcher import (
    _apply_entitlement,
    _apply_entitlement_with_tracking,
    _mark_paid_and_apply,
)

__all__ = [
    "_apply_entitlement",
    "_apply_entitlement_with_tracking",
    "_mark_paid_and_apply",
]
