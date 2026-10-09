# Academy cohort transfer review — admin runbook

## What a request means

The member-facing **Manage Academy** page records cohort change requests. A request marked `needs_review` has **not** changed cohort placement or moved any money. Re-submitting the same request is idempotent; changing destination while a request is open is blocked.

Admin > **Academy Transfers** shows the member's name and old/new cohort labels, with database IDs and payment references inside expandable technical details. **View enrollment details** uses a role-checked admin enrollment endpoint, not the member's own-enrollment endpoint.

## Resolve a confirmed-unpaid correction

1. Examine every historical payment attempt and verify against the payment provider or bank that **no money was received** under each attempt.
2. For each attempt, use **Review as unpaid** and record external closure evidence and an explanatory note. Payments Service rejects PAID/PENDING_REVIEW attempts, uploaded proofs, recorded offline receipts and fulfilled entitlements.
3. Enter an approval reason and select **Approve verified-unpaid transfer**. The Academy service independently checks that every attempted payment is explicitly closed-unpaid, that no paid installment/waived installment or recorded progress exists, and that the destination cohort is eligible and has capacity.
4. The original enrollment is preserved as `dropped`, its placement is replaced in one Academy database transaction, and the new enrollment receives the target cohort's published tuition and normal checkout. No discount is silently inherited.
5. The member sees the new cohort as a new enrollment; the original remains in programme history.

## Paid or proof-pending situations

**Do not** use unpaid closure for a real bank transfer, submitted proof, pending review, PAID checkout, or any part-paid enrollment. These cases need a separate authoritative receipt allocation/refund/credit reconciliation before completion. Current API deliberately returns HTTP 409 rather than asserting that cash was reallocated.

For the couple's shared ₦100,000 receipt, the business intent is **one verified bank receipt with two explicitly attributed ₦50,000 allocations**. Creating two unlinked PAID receipts from one transfer would double count revenue. The paid-allocation workflow is not yet available; do not manually edit tuition snapshots or waive balances to impersonate receipt settlement.

## Rejection and audit

Rejecting a request leaves the original enrollment and all payment rows unchanged. The action records reviewer identity and review time; the member sees a rejected status and may submit a new request. Raw identifiers remain accessible in technical details for investigations but are not primary navigation labels.

## Release verification

- Unit tests: rejection idempotency; paid/unclosed attempts block approval.
- Payments integration: Academy close-unpaid records audit; proof awaiting review cannot be closed.
- Combined CI: migrations, Ruff, generated OpenAPI, typecheck/build.
- Paid allocations: **still blocked** pending a ledger-backed verified receipt and split settlement workflow.
