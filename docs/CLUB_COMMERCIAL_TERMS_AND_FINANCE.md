# Commercial terms and location-first Club administration

## User-facing principle

Club sessions have published public rates. A temporary, approved commercial
adjustment belongs to a **dated transition-per-session Club enrollment**, not
to the member's identity, not to a discount code, and not to the session price
catalogue. It is not carried automatically to a new location or a new quarter.

For the AY case, complete the normal Yaba → Victoria Island transfer and Dolphin
pod assignment first. Then approve an explicit VI enrollment agreement:
standard regular practice ₦15,000; agreed regular practice ₦10,000;
valid until the 2026 enrollment expires. Do not promise or quote any extra
paid-add-on sessions at that rate. No production member changes are executed
by this code.

The agreement only affects new, regular Club practice quotes whose session
start lies inside the approved time window. Existing bookings, active payment
attempts, receipts, and settled amounts are not repriced. Visitor and paid-add-on
pricing remain host-location pricing.

## Admin sequence

1. Configure the Club's location, partner pool, and regular day/time.
2. Create a Club-wide `plan_included` recurring session template.
3. Check or create the intended pod with capacity and Pod Lead.
4. Use the standard quarter generation workflow, review every date/price,
   then publish the plan. The manual draft is secondary to the generator.
5. Transfer the member's home Club in their profile, choosing the destination
   pod. Confirm the destination enrollment exists and its expiry date.
6. Open the member's approved swim pricing editor. Set both public reference
   and agreed amount, validity and reason, then approve.
7. Verify the next *unbooked* regular practice shows the agreed checkout quote.
   Verify a paid add-on shows the full standard amount.
8. Reconcile existing unpaid/paid booking attempts independently; a newly
   approved term must not retroactively alter them.

## Academy and finance semantics

Academy keeps original nominal tuition, reviewed commercial amendments, and
verified payment allocations distinct. Management reporting displays list
value, effective contracted cash value, approved concessions, actual settled
tuition allocations, earlier cash credits, and still-due obligations where
all source data is available. It never assumes that five swimmers pay five
times the published cohort price.

Payments owns settled cash and proof-of-payment verification; Ledger owns
recognized revenue, P&L, refunds and deferrals. Contract reports are not
substitutes for either. If Payments is unavailable, financial totals stay
unknown, not zero. Old promotional discounts and historical receipt allocations
may require reconciliation before outstanding amounts can be relied upon.

The Club terms report intentionally shows the *potential per-session*
concession. Unbooked future swims do not create a receivable. Realized
concessions require joining actual frozen SessionBooking quotes and payments.

## Release gates

- Both PRs must pass CI, and schema generation must match the deployed API.
- Apply Members migration `b20261015terms` before enabling the UI.
- Confirm standard and agreed quotes in staging using a test transition Club
  enrollment, then check add-on and visitor rates.
- Confirm Academy reporting on negotiated and non-negotiated enrollment
  fixtures, including historic discount, shared bank receipt, and missing
  Payments data.
- Review responsive Admin screens (phone and desktop) and accessible inputs.
- Merge only to `develop`; production deployment remains a separate action.
