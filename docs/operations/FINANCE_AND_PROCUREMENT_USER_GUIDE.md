# SwimBuddz Finance and Store Procurement — Operator Guide

**Status:** Instructions for current implemented screens. The interface is not connected to live bank APIs. Confirm release and permission setup before using real money records. Do not invent historical entries just to make a report appear complete.

## What the system is for

- **Finance Overview** (`/admin/finance`): dated, independently attested bank balance summaries, approval queues and the 30-day planning forecast.
- **Treasury & Operations** (`/admin/finance/treasury`): bank account mappings, expense or internal-funding requests, budgets, statement imports, review and existing-ledger reconciliation.
- **Finance Team** (`/admin/finance/users`): invite and assign finance roles.
- **Finance Reports** (`/admin/finance/reports`): posted ledger statements (trial balance, profit/loss, balance sheet) and existing supplemental reports.
- **Finance Intelligence** (`/admin/finance/intelligence`): cross-business operational signals for Paystack, Academy, Club and Store. These **are not** the P&L, bank cash or automatic projections of future service cost.
- **Store Procurement** (`/admin/store/procurement`): purchase orders, receipts, stock cost verification, vendor invoices and credit requests.

## Permissions: visibility is not payment authority

| Role | Can do | Cannot do |
| --- | --- | --- |
| Finance Viewer | Review permitted finance dashboards and reports | Prepare requests, approve spending, modify journals |
| Finance Operations | Review financials; prepare expenses/funding requests, import statement evidence, enter forecast assumptions | Approve own request, independently attest balances, release bank transfers, reverse journals |
| Accountant | Accounting adjustments/reversals permitted under Ledger's own route checks; read reports | Substitute for Finance Owner approval in Treasury/Store |
| Finance Admin | Maintain permitted finance users and admin functions | Substitute for Finance Owner on Owner-only Treasury approval routes |
| Finance Owner | Budget setup, bank ledger mapping, independent treasury approval, balance attestation, journal confirmation | Approve own request; claim internal approval executes a bank transaction |
| Store Procurement Officer | Prepare and submit orders, record verified goods delivered, propose opening valuation, record supplier invoices and credit requests | Approve own purchase, confirm payments, change an unpaid order to Paid, make bank transfers |
| Store Inventory Viewer | Read the procurement workspace | Prepare or approve purchases |

Membership must be granted in the platform and kept active. **Do not give Comfort global platform Admin or Accountant simply to grant financial visibility.** The Finance Operations role is suited to preparation and financial oversight; the real bank's Initiator permission is a separate bank-controlled permission. Bank approvals still happen at Stanbic/Moniepoint.

## First-time owner setup (in this order)

1. Go to **Finance → Finance Team**. Add the finance user's correct email, choose **Operations** for Comfort, and verify the invite; confirm the user can log in and sees only permitted screens. Verify your own role is **Owner**.
2. Open **Finance → Treasury & Operations**. Check Stanbic (customer receipts/treasury) and Moniepoint (operations) have the right bank record and accounting mapping. Do **not** automatically create opening balances or change Paystack settlement destinations. Finance Owner must verify historic account mapping before linking.
3. Set separate **expense budgets** and **funding allocation budgets** for appropriate dates/categories. Sending money from Stanbic to Moniepoint is **not** business expense or revenue.
4. Keep the original bank mandate and signing authority in the bank. SwimBuddz access does not grant banking signing rights.
5. Choose an independently verified bank statement and attested closing balance. Balances displayed are dated statement figures, **never live bank balances**.
6. Before delegating Store Procurement, verify existing SKU stock counts and acquisition costs; record opening evidence for independent approval. Do not infer stock value from retail prices.

## Daily Finance routine — expense

1. Finance Operations opens **Treasury & Operations → Requests** and chooses **Expense**. Select a source bank, amount, category, business purpose and approved expense budget if any. Include vendor, invoice/receipt evidence, programme and an existing cost-centre code when applicable.
2. Owner checks supporting documentation, amount, budget and separation of duties. An unbudgeted request requires a written exception reason. **Approve** means internal approval only.
3. Comfort initiates payment through the actual bank portal as authorised by the bank's user roles; Founder approves bank release in the bank.
4. Finance imports genuine signed-date CSV statement rows from the bank; the standard columns are `date,amount_ngn,description,reference`. Debits are negative, credits positive. CSV is supporting evidence and can be fabricated, so Owner independently checks the real bank portal.
5. Owner confirms the expense against source-bank debit evidence. If the event is **already** in the accounting ledger (for example coach payout, PSP fee, refund), match/link that existing journal. **Do not post it again.**
6. Only for a genuinely unposted event, Owner chooses the reviewed expense account and posts its balanced debit/credit into the Ledger. Audit by reference, programme and supporting documents.

## Daily Treasury routine — fund Moniepoint

1. Finance Operations prepares a **Funding** request from Stanbic to Moniepoint, using a funding budget.
2. Owner approves **internally**. Transfer is executed **outside** SwimBuddz in the bank.
3. Import signed statement rows from both banks. Owner confirms **Stanbic debit + Moniepoint credit**, for the exact transfer.
4. Match an existing posted journal, or create the reviewed double-entry transfer. This changes which bank owns the cash; it does not increase or decrease profit.
5. Reconcile each bank's dated statement closing balance to the Ledger **at that same date**. Investigate discrepancies; do not invent a balancing entry.

## Weekly finance review

- Check pending requests and whether bank evidence/journal linking is missing.
- Review expenses committed vs approved budgets, and due supplier invoices.
- Compare uploaded statement rows to actual bank evidence and Ledger entries.
- Check posted Paystack gross, fees and net settlements separately from customer checkout success.
- Check Academy prepaid class entitlements and instalments separately: unused classes are a **service obligation**, not money owed.
- Maintain base / optimistic / conservative forecasts. Avoid double counting approved expense commitments in manually entered outflows.
- Report anomalies to Finance Owner; never change historical bank data to make the numbers match.

## Store Procurement — step-by-step

1. Finance Owner grants a **Store Procurement Officer** role to the correct authenticated staff ID in Store Procurement. A non-admin should see only the scoped procurement workspace.
2. Officer selects supplier, SKU, quantity and **landed acquisition cost**, then creates and submits a purchase order.
3. Another authorised Finance Owner approves or rejects. Requester cannot approve their own purchase.
4. After supplier delivers, officer records the **goods receipt**, quantity per line, delivery evidence reference (and optional PDF/image waybill upload). Over-receipts and duplicate references are blocked. Stock on hand, moving weighted-average cost, and the Ledger `DR Store Inventory / CR Accounts Payable` entry update automatically (with retry sync if Ledger is briefly unavailable).
5. Officer records the supplier invoice (with optional PDF/image upload). Once the bank transfer is approved, executed in the bank portal, confirmed, and posted in Treasury as a **Liability Settlement** (`DR Accounts Payable / CR Bank`), Finance Owner records the verified partial or full settlement on the supplier invoice linking the bank reference and posted Ledger journal UUID.
6. For historical stock without recorded cost, officer proposes an opening count/cost with evidence (and optional receipt upload), and an independent Owner approves (posting `DR Store Inventory / CR Retained Earnings`). Unvalued legacy SKUs remain fulfillable operationally and are flagged in Finance Intelligence (`unvalued_fulfilled_orders_count`) rather than fabricating ₦0 COGS.
7. On unpaid Store orders, an independently approved credit agreement (never self-approved by requester or buyer) books `DR Accounts Receivable / CR Store Revenue` and unlocks controlled fulfillment. Finance Owner records verified partial or full bank repayments (`DR Bank / CR Accounts Receivable`) until the balance reaches zero and the order is settled.
8. When a fulfilled order is returned, Finance Owner records the return lines and condition (`restockable` restores inventory/valuation and posts `DR Store Inventory / CR Store COGS`; `damaged` records shrinkage without inflating sellable stock).

## Troubleshooting

- **Store catalogue unavailable / 503:** Check Store container health and logs first; do not edit product/payment data. Verify isolated startup import guard, service image contents and the latest deployment.
- **Ledger finance page unavailable / 503:** Check Ledger image startup, organization configuration and its own migrations/health. Do not import Academy or Store Python models into Ledger.
- **403 Finance:** Confirm active Ledger finance membership, not just global-admin or Bank signatory status. Store procurement staff must have their separate grant.
- **Unvalued SKU:** Reconcile physical count and original purchase cost; no zero-cost fallback.
- **Bank mismatch:** Compare statement date, journal period, duplicate bank references and settlement timing. Attested bank balances are not live.
- **Finance Intelligence not available:** Check Ledger projection SQL against shared database schema; do not change balances or create a second journal.

**Never perform an unreviewed historical import, bank reclassification, owner self-approval or production migration to troubleshoot a dashboard.**
