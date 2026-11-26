# Finance Intelligence and Store Procurement — Architecture & End-to-End Loops (ADR-023)

**Scope**: SwimBuddz Ltd's cross-business operational finance signals, Treasury disbursement/liability controls, and end-to-end Store Procurement ↔ Ledger accounting loops.

## Ownership and Authority

| Domain | Authoritative facts | Ledger integration |
| --- | --- | --- |
| Payments | payment status, provider, verified bank transfers, Paystack settlements | emits payment, fee & settlement journals; serves `/internal/payments/finance-summary` |
| Academy | enrollments (`enrolled`, `dropout_pending`, `dropped`), class plans, installments | emits deferred revenue; serves `/internal/academy/finance-summary` (installments exclude `dropped`) |
| Members / Club | active dated Club enrollments and terms | serves `/internal/members/club-finance-summary` |
| Store | catalogue, stock, POs, goods receipts, supplier invoices/settlements, credit receivables/repayments, order returns | emits goods-receipt, opening, COGS, return-COGS, credit-AR & credit-repayment journals; serves `/internal/store/finance-summary` |
| Gateway | SwimBuddz cross-business Finance Intelligence aggregation (`GET /api/v1/admin/finance/intelligence`) | verifies caller against Ledger `/admin/finance/users/me`; zero direct DB queries |
| Ledger | detachable multi-tenant journals, CoA, periods, reports, orgs, Treasury controls | zero SwimBuddz imports, zero non-ledger SQL tables |
| Media | private S3 `finance_document` uploads (PDF/images, 15 MB) | verifies Finance viewer access via Ledger HTTP `/admin/finance/users/me` |

## Closed Store Procurement ↔ Ledger Loops

1. **Goods Receipt (`POST /procurement/orders/{id}/receive`)**:
   - Atomically updates `InventoryItem`, `StoreInventoryValuation`, `StoreCostEvent(receipt)`, and `InventoryMovement(RESTOCK)`, accepts optional `evidence_media_id`, and emits `DR store_inventory / CR accounts_payable` (`store:goods_receipt:<receipt_id>`). Failed Ledger sync marks `ledger_sync_status = "pending_retry"` with replay via `POST /procurement/receipts/{id}/sync-ledger`.
2. **Historical Opening Valuation (`POST /procurement/openings/{id}/decision`)**:
   - Independent Finance Owner approval updates `StoreInventoryValuation`, writes `StoreCostEvent(opening)`, and emits `DR store_inventory / CR retained_earnings`.
3. **Fulfilled Order COGS (`_consume_fulfilled_order_stock`)**:
   - Valued SKUs consume deterministic moving weighted-average cost (`consumption_cost_minor`) and emit `DR cogs_store / CR store_inventory` (`store:order_cogs:<order_id>`).
   - Unvalued legacy SKUs remain fulfillable operationally per ADR-023 without fabricating ₦0 COGS, logging explicit `StoreCostEvent(sale_unvalued)` rows surfaced as `unvalued_fulfilled_orders_count` in Finance Intelligence.
4. **Supplier Invoice Settlement (`POST /procurement/invoices/{id}/settlements` + Treasury `liability_settlement`)**:
   - Treasury supports `kind = "liability_settlement"` posting `DR 2110 Accounts Payable / CR Bank` once bank debit evidence is confirmed.
   - Finance Owner records partial/full settlements against `StoreSupplierInvoice` (enforcing preparer separation, blocking overpayment, verifying any linked `ledger_journal_entry_id` against Ledger over HTTP).
5. **Fulfilled Order Returns & Cost Reversal (`POST /procurement/orders/{id}/returns`)**:
   - Finance Owner (never buyer) records partial/full returns against fulfilled orders using exact historical `sale` cost events.
   - `restockable` restores `InventoryItem` + `StoreInventoryValuation` + `StoreCostEvent(return_restock)` and emits `DR store_inventory / CR cogs_store`.
   - `damaged` records `StoreCostEvent(return_damaged)` shrinkage without inflating sellable stock.
6. **Approved Store Credit Fulfillment & AR Collection (`POST /procurement/receivables/{id}/decision` & `/payments`)**:
   - Independent Finance Owner approval (never requester or buyer, including founder) emits `DR accounts_receivable / CR revenue_store` and unlocks controlled order fulfillment (`PROCESSING` → `PICKED_UP` / `DELIVERED`) while keeping `OrderStatus.PAID` gated on settlement.
   - Verified partial/full repayments (`POST /procurement/receivables/{id}/payments`) emit `DR <bank_account_ref> / CR accounts_receivable`, increment `verified_paid_minor`, and settle the receivable + stamp `order.paid_at` (and `PAID` if unfulfilled) at 100%.

## Service & Boundary Safeguards

- `tests/unit/test_service_runtime_boundaries.py`: AST import guard on `store_service`, `ledger_service`, `media_service`, `gateway_service` + static SQL table-ownership guard prohibiting any non-ledger table reference inside `ledger_service`.
- `tests/unit/test_ledger_startup_isolation.py` & `tests/unit/test_store_procurement_isolation.py`: isolated subprocess startup verification.
- `tests/integration/test_ledger_rls_and_org.py`: verifies RLS on all 22 ledger + treasury tables and explicit `X-Ledger-Org-Id` precedence.
