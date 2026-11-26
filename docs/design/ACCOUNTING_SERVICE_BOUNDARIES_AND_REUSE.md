# Accounting Service Boundaries and Detachable Multi-Tenant Architecture (ADR-023)

## Decision (November 2026 — Updated 25 Nov 2026)

**Do not create a second Finance/Accounting service.** The existing `ledger_service` *is* the standalone, detachable multi-tenant accounting engine. SwimBuddz Ltd is **Tenant #1**; other companies can be onboarded as Tenants `#2..N` via data and configuration without introducing domain-specific code into `ledger_service`.

### Strict Four-Layer Separation

1. **Accounting Core & Treasury (`ledger_service` — 100% Detachable):**
   - Owns `ledger_organizations`, `chart_of_accounts`, `cost_centers`, `ledger_periods`, `journal_entries`, `journal_lines`, `account_balances`, `revenue_recognition_schedules`, `external_transactions`, `reconciliation_breaks`, `invoices`, `ledger_users`, `ledger_audit_log`, and `treasury_*` workflows (`expense`, `funding`, `liability_settlement`).
   - **Zero domain coupling:** `ledger_service` MUST NEVER import another service's Python package AND MUST NEVER query non-ledger database tables via raw SQL or ORM (`test_service_runtime_boundaries.py` enforces both in CI).
   - **Tenant isolation & standalone DB support:**
     - Requests resolve tenant context via `X-Ledger-Org-Id` header (verified active + active `LedgerUser` membership in that org), falling back to `LEDGER_DEFAULT_ORG_ID` or sole-org fallback only when `count(orgs) == 1`.
     - Optional `LEDGER_DATABASE_URL` routes `ledger_service` (and its Alembic migrations) to a dedicated `NOBYPASSRLS` role (`ledger_app`) or standalone Postgres database without touching other services.
     - All 22 org-keyed ledger + treasury tables enforce `FORCE ROW LEVEL SECURITY` (`test_ledger_rls_and_org.py`).
   - **Vertical-agnostic configuration:**
     - Ships both `generic_smb.yaml` (zero swimming terminology) and `sports_club.yaml` CoA templates.
     - Organization onboarding (`POST /internal/ledger/organizations`) seeds any chosen CoA template, opens the current fiscal period, and creates the initial `OWNER`.
     - `GET /admin/finance/organizations` and `GET/PATCH /admin/finance/organizations/current` support multi-org listing and org-level configuration (`base_currency`, tax IDs, `protected_expense_categories`).
     - Bank cash position (`cash_position()`), deferred-revenue recognition (`recognition.py`), Treasury currency, and N-bank cash projection (`cash_projection()`) are all data/metadata-driven rather than hardcoded to Stanbic/Moniepoint/NGN.
2. **Domain Source Systems (`payments`, `academy`, `members`, `store`, `wallet`, `sessions`):**
   - Own their operational state machines and emit balanced, idempotent journal entries to `ledger_service` via `libs.common.ledger_client.post_journal_entry` (with `X-Ledger-Org-Id` support).
   - Expose authenticated `require_service_role` internal read summaries (`/internal/<service>/finance-summary`).
3. **SwimBuddz Cross-Business Read Aggregator (`gateway_service`):**
   - `GET /api/v1/admin/finance/intelligence` lives in `services/gateway_service/app/routers/finance_intelligence.py` (registered before `/api/v1/admin/finance/{path:path}`).
   - Fails closed against `ledger_service` (`GET /admin/finance/users/me` forwarding caller bearer token + `X-Ledger-Org-Id`), then aggregates service-owned HTTP summaries concurrently with partial-outage transparency (`degraded_domains`).
4. **Private Evidence Storage (`media_service`):**
   - Stores PDF/image finance receipts under private S3 (`purpose="finance_document"`, max 15 MB), accessible strictly to uploader, admins, and active `ledger_service` members verified over HTTP.
   - `ledger_service` stores only opaque `evidence_media_id` UUID references—keeping zero coupling to `media_service`.

## Pre-B2B Production Cutover Checklist (Infra Only)

The application architecture is now detachable. Before onboarding external paying companies onto a shared Postgres cluster:
1. Create a `NOBYPASSRLS` Postgres role (e.g. `ledger_app`) granted access ONLY to `ledger_service` tables & sequences (or provision a standalone Ledger database).
2. Point `LEDGER_DATABASE_URL` in production env to that role/database (keeping `DATABASE_URL` unchanged for other SwimBuddz services).
3. Run cross-tenant RLS isolation integration tests under `ledger_app` proving Org A cannot read or write Org B rows at either API or DB layer.
4. Configure per-tenant auth/SSO identity binding and billing/metering outside the accounting core.
