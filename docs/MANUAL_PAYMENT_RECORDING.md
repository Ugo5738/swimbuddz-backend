# Bank transfers and Admin payment recording

## Customer checkout

Membership, Club, Academy (including installments), session bookings and bundles,
rides, store orders, Bubbles top-ups, guest passes, Community Experience tickets,
and Stroke Lab founding payments support bank transfer alongside online payment.
Choose the method **before** creating the payment. Pricing remains authoritative
on the server; method-specific additional charges are included in the saved quote.
Bank transfer is NGN-only and cannot be combined with Bubbles. Existing product
discount eligibility is unchanged.

Manual checkout returns a private first-party transfer link. It shows the exact
amount and company bank details, accepts the bank transaction reference/date and
an optional image/PDF receipt, and submits the payment for Admin review. A receipt
is **not** confirmation of payment, a ticket, a booking, or wallet credit.

Reservation deadlines remain unchanged. The transfer page shows a saved deadline
when the owning service supplies one. Receipt submission does not extend a hold.
For expired reservations, contact Admin before sending new money. Transfers
already made can still be submitted for reconciliation. Fulfillment checks remain
with the owning service; a paid payment with failed activation needs review/replay,
never another collection.

The transfer capability is payment-scoped, sent in POST bodies and transported in
a URL fragment. It is not a Supabase auth token. The public response excludes the
payer identity, internal notes and private media URL. Guest uploads use a dedicated
receipt principal through a service-authenticated Media endpoint. Admin receipt
validation uses service-only metadata, not public file access.

## Admin recording

Go to **Admin → Payments → Record an offline payment or attach a receipt**.
Search a checkout reference or payer email. For a pending payment select
**Record verified payment**, enter the verified transfer/cash/POS details, received
time and audit note, optionally upload a receipt, then save.

This is a receipt against an **existing server-priced checkout**, not an arbitrary
amount-to-entitlement grant. The recorded allocation must equal the frozen quote.
If no checkout exists, create the appropriate product/order/enrollment checkout
first. Sessions also support **Attendance → Record paid** for an outstanding
booking without a payment intent. Outgoing coach payouts remain separate.

Only record funds you have verified. Offline settlement uses the same fulfillment
and ledger entry points as online settlement. Paid rows cannot be paid again;
retries of the same offline receipt are idempotent. Duplicate bank references and
already-paid session bookings are rejected. Mixed Bubbles/cash payments cannot
be converted to offline settlement. Do not reuse one bank reference for several
allocations through this form: combined transfers require deliberate reconciliation.

For a payment already recorded, **Attach missing receipt** adds only the file; it
does not collect money, create another payment, or reapply access. Existing receipt
evidence cannot be overwritten. A bank reference/date is valid reconciliation
evidence when no image was supplied; do not fabricate an image.

Submitted customer transfers also appear in the existing pending-review queue.
Approval records the supplied date at Lagos midnight, explicitly noting that the
exact receipt time was not supplied. Admin can instead use Record verified payment
to enter an exact time from the statement.

## Attach an Experience to a published Club quarter

In **Admin → Club Pricing**, find the published plan card and use **Attach optional
Experience**. Choose the separately published offering and save. Do not recreate
or unpublish the quarter. The attachment does not change purchased prices or add
charges to existing paid enrollments. Transition activation still excludes an
Experience; transition members purchase it separately at the standard member price.

If the section is missing, refresh the frontend and confirm that the deployment
contains the existing `AttachClubExperience` component. The link is not part of
the quarter-generation form. Publishing an Experience alone does not attach it
to a Club quarter.

## Rollout and verification

No database migration is introduced by this change. Deploy the backend services
(including Media) and matching frontend together. The code adds regression tests
for quote persistence, payment-method locking, private receipt submission,
Admin authorization, duplicate prevention, paid receipt attachment, Bubbles
top-up initialization, and zero-cost manual checkout. Existing Paystack paths
remain the default. Database-backed execution and production flow smoke tests
must still run in CI/deployment; local unit tests do not replace those checks.
