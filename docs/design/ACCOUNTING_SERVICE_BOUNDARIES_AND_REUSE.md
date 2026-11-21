# Accounting service boundaries and reusable accounting product

## Decision (November 2026)

**Do not create a second Finance/Accounting service now.** The existing `ledger_service` *is* the accounting engine. Its operational functions currently include journal posting, reversals, trial balance, statements, account mappings, Finance users, Treasury budgets and approvals. Splitting them into a second source of truth now would create inconsistency and another failure surface.

Distinguish:
- **Accounting core / Ledger** — owns immutable double-entry postings, periods, chart of accounts, organization, balances and statement semantics.
- **Finance operations** — application workflows for budgets, spending requests, bank verification and staff approvals. They can be modules using Ledger contracts; do not duplicate journals.
- **Business source systems** — Store, Academy, Members, Payments and Wallet own products, classes, orders, providers and receipts.
- **Finance Intelligence** — a read-only reporting projection, not the accounting ledger. It presently queries shared SwimBuddz database tables through raw SQL and therefore is **not detachable**.

## Current strengths

- Ledger has `ledger_organizations` with `org_id` foreign keys throughout accounting models.
- Journal entries and lines use minor currency units, idempotency keys, reversals, periods and accounting classifications.
- Ledger exposes role-based permissions separate from platform-wide admin roles.
- Isolated Docker images can deploy Ledger separately from Store.

## Current blockers to multi-business reuse

1. **Multi-tenant access is not enabled safely end-to-end.** The Ledger dependency currently selects a single default organization, and RLS is bypassed under the shared privileged PostgreSQL role. Never onboard a second legal business with this setup.
2. **Reporting is coupled to SwimBuddz business tables.** Finance Intelligence SQL reads `payments`, `enrollments`, `store_orders`, `club_enrollments`, etc. Other businesses don't have those tables, and a change to any source schema can break the report.
3. **Treasury policy contains SwimBuddz specifics.** Stanbic, Moniepoint, programme dimensions, and current source categories are defaults for SwimBuddz, not a generic accounting engine.
4. **Operational permissions span services.** Store approves selected requests by verifying Finance Owner membership over an authenticated Ledger API. Those access semantics need a stable versioned authorization contract for a product.
5. **Multi-organization data migration, billing and account lifecycle are incomplete.** Organization provisioning, isolation tests under a non-bypass DB role, automated migrations, secret management, user isolation and reconciliation controls are required.
6. **Accounting controls are not equivalent to audited statutory reporting.** Tax, multi-currency posting, deferred revenue, opening balances, refunds, inventory, revenue recognition and Nigerian compliance require jurisdiction-specific rules and professional review.

## Recommended future architecture

```
SwimBuddz Academy / Store / Members / Payments   Other business systems
                    |                                        |
           versioned validated business events / APIs
                    |                                        |
                    +--> Integration adapters <--+----------+
                                  |
                       Idempotent posting contract
                                  |
                           Accounting Ledger
              (org-isolated journals, COA, periods, policies)
                                  |
                    Ledger reports / balances API
                                  |
               Finance Intelligence read model by tenant
                                  |
                 Treasury / Procurement frontends
```

- Start with **one Ledger core** and separately packaged integration adapters.
- Publish schemas/versioned events such as `PaymentSettled`, `GoodsReceived`, `ClassDelivered`, `RefundConfirmed`, and dedupe by `org_id + source_event_id`.
- Move SwimBuddz-specific operational projections out of Ledger's runtime. Ideally the relevant source service publishes read-model snapshots; at minimum use explicit versioned SQL views and integration tests, not model imports or undeclared shared-table dependencies.
- Move business-specific UI terminology and policy defaults behind tenant configuration.
- Design truly independent organization provisioning and use non-BYPASSRLS credentials. Prove tenant A cannot see or alter B at the database and API layers, including report, logs and exports.
- Preserve accounting boundaries: one journal posting engine; other modules submit approved events, not independent balance mutations.

## Service image rule (enforced by tests)

Production Store and Ledger Dockerfiles copy `libs/` and their own `services/<service>/`, **not the full monorepo**. They must never import Python modules from one another (including imports hidden in functions). Shared Pydantic schemas/interfaces may go under independently packaged `libs/`; for runtime authorization or data, use a versioned authenticated HTTP contract or asynchronous event. Both startup isolation tests and a static AST import boundary check are required for guarded services.

## Release checks for future extraction

Do not sell or enable the Ledger as third-party accounting SaaS until multi-org isolation, audited posting invariants, tax/jurisdiction support, background settlement reconciliation, source-event delivery semantics, access revocation and operational monitoring have passed independent tests. The current Ledger is **reusable in concept**; it is **not yet a safe detachable product**.
