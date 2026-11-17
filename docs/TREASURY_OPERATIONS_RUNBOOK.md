# SwimBuddz Ltd — Treasury Operations Runbook

STATUS: staged code change, not a production bank integration.

## Bank roles
Stanbic IBTC remains the existing primary customer collections and treasury account.
Moniepoint is the operating account, funded by approved interbank transfers.
Comfort: finance-team Accountant in SwimBuddz, external bank Initiator.
Founder: finance-team Owner, external bank Approver.
An internal approval never executes a bank payment.

## Deployment and setup
1. Apply ledger Alembic migration feab17112601 in staging, with its organization-level RLS.
2. Test finance Viewer, Accountant and Owner permissions, including organization isolation.
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
Confirmed requests do not automatically post accounting entries; use existing controlled journal workflows and link them, avoiding duplicate financial postings.
Legacy /admin/payouts actions remain global-admin gated. Do not give Comfort global admin until payout segregation and bank evidence requirements are fully enforced.
Current bank balances are intentionally not shown: statement imports alone are not an authoritative current-bank-balance feed.
Bank APIs, card controls and banking signatures remain in the banks, outside SwimBuddz.
Verify the migration against staging, run integration tests and CI, and reconcile live financial records before rollout.

## Release safety
Do not modify existing Stanbic checkout destination or reclassify historical bank balances automatically.
Use separate Git feature branches, review PRs into develop, and never auto-merge other agents' concurrent changes.
Do not run migrations directly against production without verified backup, release plan and tested rollback.