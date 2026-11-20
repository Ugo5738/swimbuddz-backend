# Finance Intelligence and Store Procurement — architecture and rollout

**Scope**: SwimBuddz Ltd's unified operational finance reporting and Store
procurement. This is not a spreadsheet clone, a second general ledger,
a bank-transfer engine, or an automatic historical import.

## Ownership and authority

| Domain | Authoritative facts | Allowed writes |
| --- | --- | --- |
| Payments | payment status, provider, verified transfer, PSP settlements | existing Payments workflows |
| Academy | enrollments, purchased/used classes, installments | existing Academy workflows |
| Club | time-bounded paid enrollments and terms | existing Members/Club workflows |
| Store | stock on hand, reservations, purchase orders, goods received, customer orders | dedicated Store workflows |
| Ledger | posted journals, budgets, financial statements | Finance roles under independent approval |

Finance Viewer/Operations may read cross-business aggregates without obtaining
the global application admin role. Finance Accountant may additionally make
controlled adjustments and reversals, but may not bypass owner approvals.
Store Procurement Officer can prepare purchases, record physical receipts and
propose opening counts/credit terms, but cannot approve own purchases,
approve opening balances, approve credit, mark orders PAID, or execute bank
transfers. Finance Owner approves high-risk decisions independently.
Full admin access remains distinct from a narrow procurement grant.

The current application uses a single SwimBuddz legal organization. Before
multi-tenant launch, create org-scoped procurement grants and enforce tenant
scoping across Store/Academy/Payments read models.

## Container isolation and approval authorization

Store images contain shared libraries and Store code only. Never import Ledger
routers, models or dependency factories into Store. For owner-only grants,
purchase/opening approval and credit approval, Store authenticates the caller
and forwards the same bearer token to Ledger's existing
`GET /admin/finance/users/me`. Ledger resolves the current active finance team
membership; Store accepts only `owner`. An unavailable or malformed Finance
response fails closed for that action. The public catalogue starts and remains
independent of Finance availability. No application-admin or service-role
shortcut grants Finance Owner access. Retain separate-person approval checks;
an email-linked owner without a stored auth ID uses their authenticated user ID
for Store audit and requester comparisons.

The 19 November outage was a startup import failure in the new procurement
router, which attempted to load Ledger packages absent from the Store image.
The gateway then returned 503/name-resolution errors while Store restarted.
An isolated subprocess import test blocks Ledger imports, and authorization
checks cover denied roles, inactive membership, failures and token forwarding.
No catalogue, price, inventory, order or payment data correction is required.

## Procurement state model

- Purchase order: draft -> submitted -> approved -> partially_received -> received.
  Submitted orders may instead be rejected. Approval cannot be performed by
  the requesting person. Lines have immutable approved quantities and unit landed cost.
- Goods receipt: idempotent external reference; quantity cannot exceed the
  approved remainder; each accepted line atomically increments physical stock,
  retained acquisition value, and a stock movement/cost event.
- Supplier invoices: recorded as liabilities, not as payments. Total invoice
  value cannot exceed the approved PO. The existing bank/Treasury workflow
  must independently verify payment and link the resulting journal.
  No user-entered "paid" field is treated as proof of cash movement.
- Credit sales: independent owner-approved receivable records only. Approval
  NEVER changes a checkout order to Paid or authorises physical release.
  The existing checkout and PSP confirmation state machine stays intact.
- Cost method: perpetual moving weighted-average, denominated in integer
  minor units (kobo). Remainder stays on hand until the final unit sells.
  Each delivery retains its exact batch unit cost and evidence reference.
- Legacy stock: unknown cost is not 0. A physical count plus original
  procurement evidence must be proposed and independently approved before
  previously held stock is valued. No direct import of the illustration sheet.
- Fulfillment: a successfully paid stocked order reduces reserved quantity,
  on-hand quantity and recorded average acquisition cost when collected or
  delivered. Missing valuation does not fabricate COGS; reports explicitly
  flag missing cost history.

## Finance intelligence endpoint

`GET /api/v1/admin/finance/intelligence` is read-only, permissioned at the
Ledger viewer level. It contains provider-confirmed payments, posted Paystack
settlements and missing settlement batches; Academy current enrollments,
reviewed class obligations and overdue installments; active Club enrollments;
Store order states, verified stock cost coverage, supplier invoices and
approved credit exposure. Snapshot timestamps and data classification are
returned. This is *not* the profit/loss statement or a live bank balance.

Ledger's isolated production image runs these existing shared database reads
as aggregate SQL projections. Startup and report execution require only the
Ledger and shared library packages.

Payments gross is not bank cash. Prepaid Academy classes are delivery
obligations, not automatically accounts receivable. Inventory stock with
unverified cost is reported separately. Club future service costs remain
unknown until supplier-rate/session commitments are modelled.

## Safety and remaining integrations

- A purchase can be received without making or posting a bank payment.
  Vendor settlement remains in the existing Finance/Treasury flow.
  Exact supplier-invoice-to-bank-journal reconciliation is a separate
  controlled integration; no unpaid invoice is silently marked settled.
- Credit requires a verified settlement and a dedicated order-fulfillment
  permission before allowing an unpaid order to be fulfilled. This release
  records reviewable credit approval but intentionally does not fulfill it.
- Refunds/returns require matching reverse inventory and cost events before
  final authoritative COGS; this must be covered in staging regression tests.
- Historical batch import is not automated from the Google Sheet. All live
  opening stocks must pass a source match, count and independently audited
  valuation first.
- Cross-business reports currently use shared single-tenant DB projections;
  move to service-owned, signed operational read models when productized.
- The migration is additive and leaves preexisting rows untouched.
  Back up first and apply on staging. Confirm the existing order lifecycle,
  fulfilment, returns, pending checkout reservations, payment retries,
  reconciliation and permissions before enabling the new staff role.

## Rollout gates

1. CI: Ruff, unit/integration tests, migration heads, upgrade/downgrade,
   regenerated OpenAPI, frontend build.
2. Check Admin, Finance Viewer, Operations, Accountant, Finance Owner,
   Store Inventory Viewer and Procurement Officer permission matrices.
3. Staging: concurrent receiving, duplicate receipt, unvalued opening stock,
   separate PO approval, credit conflicts, sold item COGS, return/refund,
   verified payment and booking flows.
4. Back up production and establish a reconciled physical stock baseline.
5. Enable Store staff grants only after the historical reconciliation plan is
   signed off. Never change current checkout payment destination silently.
