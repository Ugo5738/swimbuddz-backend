"""Internal service-to-service routes for the Ledger Service.

Emitters (payments_service, wallet_service, …) post journal entries here using
a service-role JWT (validated via libs.auth require_service_role). The posting
endpoint is added in PR-2 (task P1.6). See implementation plan §5.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/internal/ledger", tags=["ledger-internal"])

# Routes added in PR-2:
#   POST /internal/ledger/journal-entries   (require_service_role)
