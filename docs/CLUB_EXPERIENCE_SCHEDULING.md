# Club scheduling and Community Experience implementation contract

This extends the deployed Club/Academy contract. Annual Membership, Club,
Academy, and Community Experience remain separate products. Existing purchases,
payment snapshots, safety assessment rules, transition session pricing, and
Academy commercial snapshots must remain intact.

## Agreed changes

1. Club is location-specific. A different home pool/cost base uses a different
   Club location; pods belong to that location, not a separate pricing tier.
2. A quarter is an individually published commercial version. Generate the first
   recommendation from a real SessionTemplate and Club defaults, then reuse that
   template for later quarters. No source Session, first-session cloning,
   automatically published subscription or automatically collected payment.
3. Link plans to actual Club Sessions. Session cost-plus pricing already owns
   pool, refreshments, other costs, expected attendance and margin. Sum the
   selected sessions' commercial prices for the recommended quarter price;
   allow an explicit final-price override and snapshot both. No global ₦5,000.
4. Entry/proration uses actual eligible, not-yet-started linked sessions, not
   weekday arithmetic. Cancelled/replaced sessions do not count. A final-price
   override is prorated using the linked sessions' frozen commercial weights.
   Already-purchased quarters/payment amounts are never rewritten.
5. Community Experience owns its package/ticket prices. Events own dates,
   logistics, venue and cost planning. Link one or multiple Events, including
   multi-day Events, without allowing a second standalone Event checkout.
6. Experience/Club scheduling impact is explicit: separate, parallel, or replaces
   specified Club sessions. Separate/parallel leave swims available for people
   not attending Experience. Q4's suggested wrap-up is the first Saturday of
   December (December 5 in 2026), not the quarter end. Admin reviews the dates.
7. Member, Club member and quarterly Club-bundle prices remain distinct.
   Admin configures guest/+1 and public-guest prices and limits per offering.
   Record each participant, waiver and paid amount separately. Public guests do
   not need annual Membership; they cannot enter their own price. Capacity must
   include held and confirmed participants, with idempotent payment fulfillment.
8. Club checkout exposes the existing bank-transfer option. A transfer already
   made is reconciled with proof and Admin approval of the server quote, not a
   second transfer or a direct enrollment insertion. Readiness is still required.

## Session identity, access and pricing

`Session` owns the actual swim and its current Admin-set `pool_fee` (kobo).
`SessionTemplate` owns recurrence and the same expected-attendance, ancillary
cost-line and margin inputs used by normal Session pricing. Both carry a soft
`club_id`, optional `pod_id`, and an explicit access mode:

| Access mode | Active quarterly prepaid member | Transition member | Quarter inclusion |
| --- | --- | --- | --- |
| `plan_included` | Free only when that exact Session is included in their plan | Current Session fee at their approved location | Counted and snapshotted when selected |
| `active_club` | Free extra practice at their covered Club/pool | Current Session fee | Never added to the purchased quarter |
| `paid_addon` | Current Session fee | Current Session fee | Never added to the purchased quarter |

Pod roster restrictions still apply. An enrollment at another Club cannot grant
access just because two Clubs use the same pool. Legacy untagged Sessions remain
readable; publication adopts a selected legacy Session into the explicit Club and
rejects another Club claiming it. Annual Membership drop-ins and Guest prices are
unchanged. Paid booking/payment snapshots never follow later Session price edits.

`ClubPlanSession` is only the inclusion ledger: soft Session ID, included pool,
original dates and commercial weight. It is not a replacement Session or a
general Club-access gate. Rescheduling the actual Session preserves this identity;
new-entry proration follows the live date and the original commercial weight.

## Admin quarter workflow

1. Configure an active Club with operating area, home pool and default weekly
   time/duration. A separate location/cost base should have its own Club record.
2. At `/admin/club-plans`, select Club, year and quarter. For an inaugural template,
   explicitly configure expected attendance and margin; pool and operating rates
   (including refreshments) come from the existing inherited pricing service.
   A missing effective rate is an error, not a guessed selling price.
3. Review actual draft Sessions, excluded dates, costs and recommended sum. Repeat
   requests reuse the same occurrence IDs. No Session price or commercial override
   is copied from an old quarter. Existing configured templates are editable in
   the Admin Sessions template drawer; their future dates get newly quoted costs.
4. Select the actual inclusions, optionally override the final quarter price,
   and optionally link the separate Experience. Missing refreshment lines produce
   a review warning, not a second Club refreshment tariff (free/sponsored is valid).
5. Explicitly publish after review. Schedule, pool, end time and price changes
   require re-review. Future quarters remain independently selectable by members.

There is no scheduled automatic generation/publication task: Admin requests a
recommendation when needed. A saved template removes repetitive Session creation.
Q4's December 5 suggestion does **not** automatically exclude that day's swim.
Keep it for non-attenders, or deliberately exclude it before selling the quarter.

## Pod and published-schedule operations

`/account/pod-lead/sessions` is available to active leads/assistants. Members-service
verifies the lead assignment and active Club eligibility; Sessions-service owns
the resulting swim. Extra practice requests accept no price, pool, access mode,
duration or capacity override. They use the configured pod pool/capacity and an
Admin-approved inherited-pricing template. The lead confirms pool availability.
Overlapping practices for the same pod are rejected.

Use **Reschedule swim** from a published quarter, Admin Session editor, Experience
impact editor, or Pod Lead practice page for rain/makeup/Experience clashes. It:

- Retains the same Session ID, pool, duration, current Session price, inclusion
  ledger, bookings, guest passes and historical paid amounts.
- Requires a future date, reason and confirmed pool time. A published promise
  must remain within every linked purchased quarter at its included pool.
- Records actor and before/after schedule, is idempotent per operation ID, and
  notifies booked members in-app and standalone guests by operational email.
  Members are asked to notify guests in their booking and contact Admin if unable
  to attend. Failed notification delivery remains visible/retryable after reload;
  superseded changes are not sent as if they were the current schedule.

Direct cancellation/deletion cannot remove a published quarter's inclusion.
Published Club timing/scope changes use this route, not ordinary edits. Different
pool/quarter substitutions, compensation and refunds require a separate approved
coverage agreement; this implementation does not invent a refund policy. Review
transport/volunteer arrangements separately when moving a swim; they are not
silently rebooked by the reschedule operation.

For an Experience clash after publication, reschedule the promised swim and use
Separate/Parallel for the Experience. `replaces` is only for explicitly selected
future unbooked swims not promised in a published quarter. Other swims remain.

## Experience configuration recovery (service APIs, not shared transactions)

Events own venue, itinerary, date range, cost planning and reminders. An offering
can package one multi-day Event or several Events; the current package validator
requires them within its commercial period. Cross-quarter travel is not silently
reassigned to another quarter: configure the offering's commercial period to cover
the actual trip; it can be bought standalone, while Club bundles require matching
periods. No separate child/VIP/transport ticket catalogue is implied.

Linking validates local terms and affected swims before binding. Members stores a
durable `pending` configuration operation, Events binds the full Event set, Sessions
applies any permitted replacements, then Members commits the local links as
`applied`. An error restores the old binding and undoes the unsold replacements.
Each service stores operation IDs; compensation fences delayed requests so a
timed-out write cannot reapply after recovery.

If restoration succeeds, status becomes `failed` with the previous configuration
intact. If a service cannot restore, it becomes `needs_reconciliation`; crashes
may leave `pending`. Both block new configuration and checkout. Use the offering's
**Configuration status and recovery** card to restore the previous configuration,
then submit a fresh attempt. Local SQL never reads another service's tables, and
there is no distributed SQL transaction or prepare/commit protocol.

## AY's existing transfer

Publish an appropriate Club plan, reuse/record genuine observed readiness, approve
the intended payment arrangement and expiry, and open AY's approved checkout.
Choose the existing manual-transfer option and submit the actual transfer proof;
Admin checks the server quote against the amount received and approves through
normal payment fulfillment. Do not send AY through annual Membership checkout
again or insert an enrollment by hand. For transition approval, there is no
applicant-specific Session rate and no bundled Experience charge. Any discrepancy
in what AY paid needs Admin reconciliation; this code does not assume the amount.

## Verification boundaries

Use non-database unit/component tests, TypeScript, lint, OpenAPI freshness,
production compilation, and offline migration SQL checks locally. PostgreSQL
integration tests are written for CI; Docker and production data are out of scope.
No automatic production configuration, paid booking cancellation, refund, or
branch cleanup is authorized by this implementation.

New append-only migration heads for this review: members `e9f1a3b5c726`, sessions
`b4d8f0a2c613`, events `a3b5c7d9e012`. Their predecessors remain intact. Deployment
must apply all three services' migration chains before serving the new code.
Review-branch pushes run the normal CI test job without production image/deploy
jobs. Local unit/component results do not substitute for PostgreSQL CI results.
