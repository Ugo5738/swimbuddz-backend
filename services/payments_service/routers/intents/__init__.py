"""Payments intents router package.

Original `routers/intents.py` was 2,333 lines covering pricing, intent
creation, member-driven Paystack verify, admin completion, replay, plus
the entitlement-application machinery and Paystack/discount helpers. Per
docs/CONVENTIONS.md §12, that's well past the hard cap for a router file
and far past the comprehension cap for a single module.

Split into a package with one helper module per concern + one router
module per endpoint group. The public surface used by sibling routers,
tasks, and tests is preserved via re-exports below.

**Re-exported symbols** (kept stable for `webhooks.py`, `internal.py`,
`tasks.py`, `manual.py`, and existing tests):
  - `router`                          — the FastAPI router (prefix /payments)
  - `_apply_entitlement_with_tracking`
  - `_mark_paid_and_apply`
  - `_verify_paystack_signature`
  - `_verify_paystack_transaction`
  - `_initialize_paystack`
  - `_paystack_enabled`, `_paystack_headers`, `_to_kobo`, `_callback_url`
  - `_try_qualify_referral`
  - `internal_post`, `logger`         — patched by unit tests

**Route ordering** within `/payments`:
  - `/pricing`                          (literal)
  - `/intents`                          (literal)
  - `/me`, `/paystack/verify/{ref}`     (literal-prefixed member surface)
  - `/{reference}/complete`             (2-seg catch-all; safe because the
                                         other 2-seg routes are literal-only
                                         or 3-seg)
  - `/admin/...`                        (literal-prefixed admin surface)

Submodules:
  - _constants.py-equivalent lives inline in each module (each defines
    `settings = get_settings()` and `logger = get_logger(__name__)`);
    `logger` and `internal_post` are also re-exported here for legacy
    test patches.
  - _paystack.py        Paystack API + HMAC helpers
  - _helpers.py         generic notification / metadata helpers
  - _discounts.py       discount lookup + application
  - _entitlement.py     `_apply_entitlement` (the 869-line dispatcher
                        across 9 PaymentPurpose values) plus the two
                        wrappers that route handlers actually call.
                        Stays large by intent — splitting per-purpose
                        would change semantics & cross-service contracts.
  - pricing.py          GET /pricing
  - intent_creation.py  POST /intents
  - member_payments.py  GET /me, POST /paystack/verify/{reference}
  - completion.py       POST /{reference}/complete
  - admin.py            DELETE /admin/members/by-auth/{auth_id},
                        POST /admin/{reference}/replay-entitlement
"""

from fastapi import APIRouter

# Re-exports for legacy callers & test patches. Importing them here makes
# them available as attributes of this package, e.g.
# `services.payments_service.routers.intents._verify_paystack_signature`.
# DO NOT remove without auditing webhooks.py / internal.py / tasks.py /
# manual.py and the test patch paths.
from libs.common.logging import get_logger
from libs.common.service_client import internal_post  # noqa: F401  re-exported

from . import admin as _admin
from . import completion as _completion
from . import intent_creation as _intent_creation
from . import member_payments as _member_payments
from . import pricing as _pricing
from ._entitlement import (  # noqa: F401  re-exported
    _apply_entitlement,
    _apply_entitlement_with_tracking,
    _mark_paid_and_apply,
)
from ._helpers import _try_qualify_referral  # noqa: F401  re-exported (tests)
from ._paystack import (  # noqa: F401  re-exported
    _callback_url,
    _initialize_paystack,
    _paystack_enabled,
    _paystack_headers,
    _to_kobo,
    _verify_paystack_signature,
    _verify_paystack_transaction,
)

logger = get_logger(__name__)  # noqa: F401  re-exported (tests patch this)

router = APIRouter(prefix="/payments", tags=["payments"])

# Order matters: register static-literal-prefixed sub-routers before
# the one containing `/{reference}/complete` so FastAPI's match-order
# never accidentally captures e.g. `/me` as `{reference}`. Within each
# included sub-router, declaration order is preserved by FastAPI.
router.include_router(_pricing.router)
router.include_router(_intent_creation.router)
router.include_router(_member_payments.router)
router.include_router(_completion.router)
router.include_router(_admin.router)

__all__ = [
    "router",
    "_apply_entitlement",
    "_apply_entitlement_with_tracking",
    "_mark_paid_and_apply",
    "_verify_paystack_signature",
    "_verify_paystack_transaction",
    "_initialize_paystack",
    "_paystack_enabled",
    "_paystack_headers",
    "_to_kobo",
    "_callback_url",
    "_try_qualify_referral",
    "internal_post",
    "logger",
]
