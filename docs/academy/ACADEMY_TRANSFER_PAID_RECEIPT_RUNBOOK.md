# Academy paid cohort transfers and pooled bank deposits

## Accounting boundaries

- Payments Service owns the *one* bank receipt and posts *one* cash-in journal entry.
- Each `AcademyReceiptAllocation` reserves a beneficiary-specific portion of that cash. Allocations **do not post income**.
- The Academy Service owns tuition obligations and applies each allocation as an idempotent `AcademyFinancialCredit`, tied to an immutable `source_reference`.
- Verified tuition already recorded as a paid Payment is also used when transferring between cohorts. Never recreate a paid Payment to represent reassigned tuition.
- The original enrollment, attempts, submitted POP, awards, milestone-review history and attendance remain traceable after a transfer.

## One ₦100,000 bank receipt, two ₦50,000 swimmers

1. Confirm the transfer *actually arrived* by independently checking the SwimBuddz bank statement. Only an authorized admin may enter verified evidence.
2. Under **Admin → Shared Academy Receipts**, register the bank transaction *once*, with its unique external reference and **₦100,000** total. This produces one canonical PAID receipt in Payments and one ledger cash-in.
3. Select each swimmer's correct existing Academy enrollment, reserve **₦50,000** and click **Apply**. The payments service derives the member identity from Academy; it is not accepted from the browser. The receipt balance decreases from ₦100,000 to ₦50,000, then ₦0.
4. If a learner had an earlier `PAY-...` bank checkout or POP, explicitly link that payment attempt to the **applied** allocation from this one receipt. The original proof is retained; the superseded attempt is closed with a reconciliation marker (not falsely described as unpaid). Old payment or provider callbacks cannot activate the enrollment again.
5. The student requests a cohort change from **Manage Academy**. An existing paid or credit-bearing enrollment is held for review.
6. Under **Admin → Academy Transfers**, review the verified tuition, payments, unmatched attempts, milestone and attendance data, and the target published tuition.
7. Specify approved unused credit and the separately reviewed value of any services already consumed, with `unused + consumed = verified tuition`. Use **explicit** destination discount and reason; no prior discount is portable automatically.
8. Confirm attendance review, approve and open the new enrollment. The source is retained as DROPPED; past classes remain in their original cohort and relevant milestone evidence is projected onto the destination. Tuition credits move through source-linked rows; cash income never changes.
9. The destination checkout charges only the current unpaid installment obligations. E.g. ₦165,000 tuition less verified ₦50,000 credit leaves **₦115,000**, before any separately approved destination discount.

## Exceptions and protections

- **No money received:** use the pre-existing audited checkout close-unpaid path and approve-unpaid transfer. Never use it for submitted POP or actual receipts.
- **A reserved allocation was wrong:** void only the still-reserved item, with a reason. A credit already applied to a student's tuition cannot be voided using this action.
- **Duplicate bank references:** verification rejects another record carrying the same reference.
- **Applied allocation timed out:** retry the same allocation's Apply action; Academy credit source references are globally unique and replay returns the prior result.
- **Old PAID payment:** cannot be re-entered as a shared receipt for the same bank reference. Resolve its source attribution before creating extra revenue.
- **Refund or excess cash:** the reviewed transfer refuses credit beyond destination tuition, and will not hide a surplus as a tuition discount. Follow the existing verified refund process and ensure finance documents its source, owner and disbursement. The destination transfer remains blocked until the financial resolution is complete.
- **Refunded/chargeback/unknown payment state:** payment state becomes ineligible for automatic paid-transfer approval. Resolve with Payments first.
- **Late webhook:** the old enrollment is not eligible for new entitlement, and the superseded checkout is marked as reconciliation rather than applying a second credit.
- **Concurrent actions:** the receipt row is locked while allocating; the change and source enrollment are locked during review and completion.
- **Journey history:** preserve the historical enrollment row and completed change snapshot; never rewrite a paid source row's cohort ID.
- **Unverified bank confirmation:** do not create any receipt solely based on a member screenshot. Confirm against SwimBuddz's account/provider.

## Release checklist

- Apply new Payments and Academy migrations; confirm a single Alembic head per service.
- Verify OpenAPI regeneration and frontend client typings use the same backend commit.
- Pass all shared-receipt, credit, and paid-transfer integration tests; run baseline unit/entitlement/booking test suites.
- Validate admin role permissions, member ownership, receipt uniqueness, zero over-allocation, retries, legacy POP, and late webhook.
- Confirm the bank-receipt and transferred-tuition reporting does not count the same cash as two receipts.
- Production deployment requires separate review; never manually change Tuyo's enrollment or payment records before the migration and UI are released.
