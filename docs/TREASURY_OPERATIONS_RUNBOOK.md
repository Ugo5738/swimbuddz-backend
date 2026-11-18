# SwimBuddz Ltd — Treasury Operations Runbook

STATUS: staged code change, not a production bank integration.

## Bank roles
Stanbic IBTC remains the existing primary customer collections and treasury account.
Moniepoint is the operating account, funded by approved interbank transfers.
Comfort: finance-team Operations in SwimBuddz, external bank Initiator. This avoids granting journal adjustment and reversal privileges.
Founder: finance-team Owner, external bank Approver.
An internal approval never executes a bank payment.

## Deployment and setup
1. Apply ledger Alembic migration feab17112601 in staging, with its organization-level RLS.
2. Test finance Viewer, Operations, Accountant and Owner permissions, including organization isolation.
3. Owner registers the two bank records in Finance > Treasury & Operations.
4. Verify the existing bank_operating_ngn ledger attribution before linking Stanbic.
5. Create the dedicated Moniepoint bank ledger account, leaving Paystack settlement mappings alone.
6. Comfort prepares expense/funding requests, founder approves in SwimBuddz.
7. Comfort initiates the actual transfer in Stanbic; founder approves through the bank.
8. Independently obtain authentic bank statements and convert to reviewed CSV template: date,amount_ngn,description,reference (signed NGN, ISO dates).
9. Import at most 250 rows per batch, verify conflicting rows are rejected, and review both statement legs before confirming internal funding.
10. Reconcile imported lines against the existing journal, without double posting previously booked payment or settlement events.

## Accounting status distinctions
Requested: internal proposal, no payment.
Approved: internal decision only, no bank execution.
Confirmed: bank statement evidence has been linked by Owner; check the real bank portal independently.
Matched journal: bank evidence linked to an existing accounting entry.
CSV inputs are uploaded evidence and may be fabricated; they are not direct bank verification.
Internal transfers are NOT revenue or expense.
Existing customer collection, Paystack fee, Bubbles and coach payout flows must remain intact.

## Known remaining work before production
Confirmed requests have a separate owner-only Post to Ledger action. It uses existing post_entry with a stable idempotency key. Funding debits Moniepoint and credits Stanbic; expenses debit selected valid expense account and credit actual bank account. Never book a second entry for coach payouts, PSP fees, refunds, or previously posted transfers.
Legacy /admin/payouts actions remain global-admin gated. Do not give Comfort global admin until payout segregation and bank evidence requirements are fully enforced.
Current bank balances are intentionally not shown: statement imports alone are not an authoritative current-bank-balance feed.
Bank APIs, card controls and banking signatures remain in the banks, outside SwimBuddz.
Verify the migration against staging, run integration tests and CI, and reconcile live financial records before rollout.

## Release safety
Do not modify existing Stanbic checkout destination or reclassify historical bank balances automatically.
Use separate Git feature branches, review PRs into develop, and never auto-merge other agents' concurrent changes.
Do not run migrations directly against production without verified backup, release plan and tested rollback.
## Operational approvals, reports and monthly controls

- Register the two bank accounts with the finance Owner. After checking historical entries, explicitly link the existing NGN bank account to Stanbic. Moniepoint uses its own dedicated ledger account. A registration is not an opening balance.
- Comfort submits a bank transfer or expense request with amount, programme, optional existing cost centre, vendor, invoice and receipt evidence reference. Fund transfers have a funding budget distinct from expense budgets.
- Founder approves internally. Comfort initiates externally. Founder authorises at Stanbic. No bank credentials or bank transaction tokens reside in SwimBuddz.
- Founder independently verifies authentic statement credits/debits, then marks the request confirmed with evidence. Finally the Founder can choose Post to Ledger. The code blocks duplicate bank reference/amount matches and existing journal-linked evidence. Uncertain cases must use manual ledger reconciliation.
- Coach payouts, refunds and PSP fees already have dedicated emitters. Never create a second treasury expense journal for them. The legacy payout handler still requires operator oversight; future bank-source attribution must be reviewed before routing coach payouts through Moniepoint.
- Use the bank reconciliation status view: an attested snapshot is compared against posted ledger journal lines at the *same statement date*. A mismatch means open investigation, not a fabricated bank movement.
- Finance dashboard shows verified statement dates, unmatched transaction counts, pending approvals and expense records. It does not claim live bank balances.
- Cash forecasts use explicitly entered scenario assumptions plus separate approved expense commitments. Do not add the same expense as a manual outflow assumption. A projection is withheld until both bank snapshot dates agree. Forecasts are planning aids, not financial statements.
- Budget utilization separates committed approved requests, confirmed requests and remaining budget. Funding budget is a treasury ceiling and not an expense.
- Do not deploy before staging migrations, API authorization tests, finance UI CI, and replay of critical checkout/payout/refund flows. Roll out only after latest develop changes and independent finance review.
