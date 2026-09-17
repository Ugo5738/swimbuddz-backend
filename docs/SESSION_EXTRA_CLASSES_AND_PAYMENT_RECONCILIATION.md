# Extra classes, shared costs and payment reconciliation

## Regular tuition versus extra practice

`Session.cohort_fee_mode` is the explicit billing policy for enrolled students:

| Mode | Booking price | Use |
| --- | --- | --- |
| `included` | ₦0, regardless of `pool_fee` | Regular classes paid through tuition; included make-ups/complimentary practice |
| `paid_extra` | Current Admin-configured `Session.pool_fee` | A separately agreed extra class |

Enrollment/suspension checks still apply to both. Guest charges remain independent.
No classification is inferred from the title, week number, capacity or pool cost.
The initial cohort generator and extension generator create included classes.
Both Admin Sessions and the cohort's Add Session form expose **Class payment**.
Publishing controls visibility; a draft is not bookable.

For an extra: choose the cohort, select **Paid extra class**, set the per-student
price (manually or using cost-plus), capacity, dates and pool, then publish.
An eligible student can use `/sessions/{id}/book` or their sessions/dashboard
entry. Booking snapshots the server-resolved price and remains pending until
payment succeeds. Normal Paystack, eligible discounts and Bubbles apply. The
payment handler automatically confirms and links the booking; retries are
idempotent. Failed or abandoned payment does not create a paid booking.
An included class confirms at zero without creating a second tuition payment.
Ride-share and guests may still make that checkout payable.

Migration `c6e8a0b2d914` follows `b4d8f0a2c613`. Every existing Session defaults to
`included`, deliberately protecting regular classes with nonzero stored costs.
No booking or payment is backfilled/repriced by this migration. Designate any
previously created paid extra explicitly after deployment. Apply the migration
before starting the new sessions-service; deploy frontend and backend together.

## Cost-plus is an optional costing tool

Manual pricing means entering what each student pays. No division is applied.
Cost-plus sums cost-line unit price × quantity, divides the total cost by expected
attendance, then adds a per-person margin (fixed or percentage). Quantities are
explicit: two swimmers at ₦6,300 plus one staff travel cost of ₦3,500 means
₦16,100 total, or ₦8,050 per swimmer before margin. Charging ₦9,800 before margin
would require ₦3,500 travel **per swimmer**, or two staff journeys at that rate.

Expected attendance must not exceed capacity. Reducing capacity updates/clamps
the estimate and automatic per-attendee quantities. Deliberate quantity overrides
remain editable and are labelled. Actual turnout never retrospectively reprices
an existing booking. For included Academy classes the student booking price is
still zero, even though the operating-cost calculation is nonzero.

## Existing bank payments and past attendance

Do not represent money already received as a discount or an unaccounted wallet
credit. A discount reduces revenue; a verified transfer records incoming money.
Coach payout completion and incoming session payments share the accounting/ledger
system, but remain distinct outgoing `CoachPayout` and incoming `Payment` records.

1. Locate the actual historical Session (not the member's intended future Club).
2. In **Admin → Attendance**, select it. **Record attendance for a member not on
   this roster** can find a registered member, including one registered after the
   swim. Record the agreed session fee and an audit note. This records attendance,
   not payment or Club enrollment. Existing bookings retain their saved amount.
3. Use **Record paid** on the outstanding booking. Enter verified method, original
   received-at date/time, bank reference and note. The existing offline-payment
   endpoint creates the incoming Payment and runs normal fulfillment/ledger
   processing. Duplicate receipts/settled bookings are rejected. A payment-link
   is appropriate only for a fee that has **not** already been paid.
4. Annual Membership/Club activation uses the approved product quote and existing
   bank-transfer proof/review flow. An admin's review note can document the original
   bank reference/date and allocation. The review timestamp is not evidence of
   the original transfer date. Do not approve an entire quote using a transfer
   that only covered one component. Allocate combined transfers once: e.g. a
   ₦25,200 credit may cover ₦20,000 Membership plus a ₦5,200 swim, not two ₦25,200
   payments. Choosing bank transfer still requires receipt review/approval.

Annual Membership checkout now exposes Bank Transfer directly. Billing's
**Already paid by bank transfer? Submit your receipt** opens that method and
**Continue to upload receipt** creates a pending reference, then opens Billing's
receipt section. Neither action marks the payment paid. The receiving account
display is shared across frontend checkout, Billing and session sign-in:
Swimbuddz Limited, Moniepoint MFB, 6567710856. Historical receipts sent to an old
account must still be checked against the original bank statement; the display
change does not rewrite payment history.

For a combined receipt, review each allocation explicitly. A 20,000 Membership
allocation and a 5,200 session allocation must not be recorded as two full 25,200
payments. The existing Admin **Record paid** supports session receipts; the
Membership receipt/review flow remains separate. Deployment does not reconcile
either automatically. Membership activation currently begins at approval (or
the existing later expiry), not the original transfer date; historical coverage
dates need an explicit Admin review, not an inferred or silent backdate.

The receipt viewer resolves the stored media UUID through authenticated media
access. Only uploader/admin can resolve payment-proof media, including legacy
`payment-proofs` paths; anonymous media lists and batch URL resolution exclude it.
Submitting the same receipt again after a lost response is idempotent.

### Legacy zero-price extra-class bug

For an old confirmed booking saved at zero with no access/price source, no wallet
or payment link and one attendee: first designate its Session `paid_extra`.
Then **Correct missing class fee** allows an explicit, audited correction to the
originally agreed amount. It does not charge money, change attendance or record
a receipt. Afterwards use **Record paid** for verified funds already received.
Paid/already-priced bookings, deliberately included classes and deleted/missing
sessions are rejected. Do not move an orphan booking to a replacement session
silently, and do not charge it retrospectively through an automated backfill.

## Template retirement

Archive turns off `is_active` and `auto_generate`; existing sessions/bookings stay
intact. Archived templates cannot generate sessions and are hidden by default.
Show archived templates to restore or permanently delete. Restore does not turn
on automatic generation. Permanent delete succeeds only when unreferenced;
referenced templates return 409 with guidance to archive, not an internal error.

## Verification

Unit/contract tests cover included nonzero-cost classes, paid-extra pending and
Bubbles bookings, payment-linked confirmation, frozen booking prices, explicit
legacy correction guards, shared-cost math, archive/delete and receipt privacy.
Frontend tests cover both class-creation screens, the real booking page (including
discount/Bubbles payloads and verified return), past-attendance search and receipt
review. PostgreSQL-backed tests/migration execution require CI or a test database;
SQL generation alone is not evidence that production migration succeeded.

### Existing reporting limitation (unchanged)

The current Payments ledger mapping classifies `session_booking` cash receipts
under `revenue_club_session` with the Club dimension, regardless of Session type.
This patch verifies booking/payment linkage, not a new Academy-versus-Club revenue
allocation policy. Product-specific ledger classification (including Bubbles and
refund symmetry) needs a separate consistent correction; historical journals
must not be silently rewritten. This limitation also predates paid-extra mode.
