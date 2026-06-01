"""Admin / finance routes for the Ledger Service.

Role-gated per design doc §15 (viewer/accountant/admin/owner). Reports, manual
journal entries, reversals, and finance-user management are added in PR-3
(tasks P1.6b, P1.7, P1.11). See implementation plan §5.

Gateway proxies /api/v1/admin/finance/{path} → /admin/finance/{path}.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/admin/finance", tags=["ledger-admin"])

# Routes added in PR-3:
#   GET  /admin/finance/accounts                       require_ledger_role("viewer")
#   GET  /admin/finance/journal-entries                require_ledger_role("viewer")
#   POST /admin/finance/journal-entries                require_ledger_role("accountant")
#   POST /admin/finance/journal-entries/{id}/reverse   require_ledger_role("accountant")
#   GET  /admin/finance/reports/trial-balance          require_ledger_role("viewer")
#   GET  /admin/finance/reports/profit-loss            require_ledger_role("viewer")
#   GET/POST/PATCH/DELETE /admin/finance/users         require_ledger_role("admin")
