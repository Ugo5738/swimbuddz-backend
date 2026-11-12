# Esther's reviewed preorder correction — 12 November 2026

The owner requested correction of order `SB-20261111-FATLF` from black
HD Mirror Coating goggles (₦10,000) to black Electroplated Anti-Fog Swim Goggles
with Earplugs (`SB-GER-001-BLA`, ₦15,000). The cap and kickboard stay unchanged;
the order becomes ₦42,000. Both original and replacement products are preorders,
so this correction must not fabricate physical stock or inventory movements.

The owner confirmed on 12 November that the original ₦37,000 and separate ₦5,000
both reached the bank on 11 November. The supplied top-up bank reference is
`45ddcf4f-3dc1-4440-95f7-8913a8ac3d2e`. The original bank reference must be taken
from its existing evidence or supplied by the owner, never invented.

## Prepare and apply

Run `scripts/ops/correct_esther_goggles_20261112.py` without flags for a read-only
preview. `--apply --actor-label "<operator and owner authorization>"` performs the
scoped repair in one database transaction. Run only the reviewed main-branch
script. No runtime service source or database schema is changed.

This maintenance script deliberately coordinates the two affected aggregates
atomically; it is not imported by a service or used as a cross-service runtime
integration. It checks the exact order, member, item, current price, preorder
status, existing receipt and absence of extra payments. Changed or paid state
aborts. Repeated apply is a validated no-op, not another top-up.

The original ₦37,000 payment amount, proof and transfer submission are retained.
A new UUID4 payment stages the ₦5,000 claim as `pending_review`. Both payments
carry `checkout_reconciliation` holds, understood by the existing fulfillment
dispatcher. These hold fulfillment, not cash accounting. The script does not
approve receipts, set `paid_at`, post journals, email anyone or mark the order paid.

## Verify the receipts and release fulfillment

Use each payment's existing Admin offline-payment endpoint, with its exact frozen
amount (3,700,000 and 500,000 kobo), actual unique bank reference, Lagos received
date and audit note. Use the existing original receipt; do not create an extra
₦42,000 cash payment. Each verified receipt posts through normal payment-ledger
settlement exactly once. Missing bank references remain pending review.

After both receipts are `PAID`, run the script with `--release` to preview, then
`--release --apply --actor-label ...`. It checks independent bank references,
amounts, identities, currency and the complete ₦42,000 total, and removes only
the primary receipt's fulfillment hold. The top-up remains a non-fulfilling
component; it must never independently mark the order paid.

Finally call `POST /payments/admin/{original_reference}/replay-entitlement` using
normal authorized Admin/service authentication. This replays fulfillment only;
it does not post cash again. Inspect `entitlement_applied_at`, the corrected order
items/status, both receipt amounts and ledger entries. Further release calls are
idempotent. After fulfillment, normal order processing continues.

Finance should contain ₦37,000 + ₦5,000 in store receipts, while the order and
product sales show the corrected ₦42,000 order and ₦15,000 goggles. Pending proof
alone is not paid revenue. Existing store-report status/tender limitations are
outside this repair; use payment receipts and the ledger to reconcile cash.
