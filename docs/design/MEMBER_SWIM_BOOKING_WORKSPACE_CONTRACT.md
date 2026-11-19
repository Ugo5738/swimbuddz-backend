# Member swimming booking workspace: implementation contract

Status: **incremental implementation / not production-ready** (19 November 2026)

## Product entry points

- **Book My Next Swim** is a high-frequency shortcut. The Sessions backend recommends one already-published, eligible, unbooked Session and the member goes straight to that Session's existing checkout. A recommendation never charges money or reserves a seat.
- **My Swims** combines list, date-selectable calendar, confirmed reservations, Academy assignments and full personal history. A member can select multiple existing Sessions into the existing SessionBundleCart checkout. Frontend prices are *estimates*; the Payments/Sessions checkout is authoritative.
- **Find a Swim** preserves discoverability of SwimBuddz sessions, pool directory (including non-partner facilities), independent Pool Access, Academy, events and member-hosted Open Swims. Calendar-selected dates prepopulate discovery.
- **SwimBuddz Calendar** remains a navigable published-activity projection. The authenticated viewer can filter to confirmed Sessions and Academy assignments, or combine both views. Do not treat a discoverable event as a confirmed reservation.

Never replace or regenerate existing AttendanceRecord, SessionBooking, payment ledger/intent, AcademyClassAllocation, ClubPlanSession inclusion or session media to populate calendars. Present links/projections of the authoritative records. The original [ADR-011] and event-backed-session boundaries remain valid.

## Booking type ownership

| Activity | Domain owner | What constitutes a confirmed commitment? |
| --- | --- | --- |
| Existing Club, Academy extra, Community or linked-Event Session | Sessions | Confirmed SessionBooking or explicitly assigned Academy/Club inclusion |
| Independent partner-pool admission | Pools | Confirmed paid PoolAccessBooking with valid admission terms/ticket |
| Member-hosted peer Open Swim | Events | Published event and attendee RSVP, **not** a guaranteed facility booking |
| New coached session requested by a member | No supported universal flow yet | Confirmed staffing, pool allocation, policy, then an operational Session |
| Community Event linked to a Session | Events (details), Sessions (operations) | SessionBooking is the payment/attendance owner, not a second Event RSVP |

## Pool availability guarantees

A pool directory record is **discovery**, not availability. Existing Pool.available_days_times is indicative only. The new GET `/api/v1/pools/access/availability` reports remaining capacity **only for approved, published Pool Access offers**, including current confirmed bookings and unexpired checkout holds. Its response intentionally states `inventory_scope=published_pool_access_offers_only` and `reservation_guarantee=none_until_checkout_hold`.

It does **not** check competing Academy Sessions, Club Sessions, public-pool external bookings, lane inventory, coach rosters or a member-hosted Event's true resource allocation. Never re-label it as live whole-pool availability.

Before self-created, arbitrary-date paid Sessions can be offered:
1. Define Pools-owned resource inventory for whole-pool, lane group and shared-admission capacity; normalize IANA timezones, setup/turnover buffers, partner blackouts and release windows.
2. Define a Pools-owned claim/hold/confirm/release state machine with idempotency, expiry, atomic conflict checking, fair sharing between unrelated activities and auditable partner authorization.
3. Have Sessions/Events obtain and retain resource-allocation references before publishing guaranteed slots. When coach-led, separately verify coach availability, curriculum suitability, required supervision and maximum swimmer capacity.
4. Only return `instant_bookable` for explicitly partner-authorized inventory. Otherwise return `request_required` and prohibit a paid/confirmed promise until approved.
5. Generate server-side member-specific quotes and snapshot price/entitlement/payment conditions before the customer commits. On payment failure, safely release holds; on ambiguous provider success, reconcile before retrying charges or seats.
6. Add cross-service recovery for partial failures, and tests for concurrent checkout, cancellation, reschedule, paid-but-not-fulfilled, double-billing and pool partner independent sales.

## Mixed checkout

The existing Sessions bundle supports up to 10 sessions and server-side multi-reservation/quote + one payment. Pool Access has a separate admission and verification lifecycle. A single mixed-domain checkout is **not enabled** until a durable order/settlement coordinator provides atomic customer outcomes, idempotent per-domain fulfilment and explicit partial-failure/refund/reconciliation handling. Do not fake a combined order in the frontend.

## UX/history safeguards

- Calendar day selection should never claim capacity from published activity projections.
- Only display a final, payable total from the authoritative backend; frontend basket totals are estimates.
- Included Academy/Club commitments must not be charged again. Exclude already-confirmed Sessions from quick-booking recommendations.
- Past bookings, attendance and media remain separate; dates in the past do not imply attendance.
- Pool Access paid cancellations currently require review; do not silently invent immediate refunds or allow self-reschedules against unapproved policies.
- Preserve old routes, analytics attribution and links. Show actionable partial-data errors rather than fabricating confirmed booking statuses.

## Release gates

- Generate/update `openapi.json`, rerun frontend generated types, lint, TypeScript and production builds.
- Verify personal history for an Academy learner, quarterly Club member, transition member, annual Community member, visiting Club member, and a user with multiple programme roles.
- Verify bundled payments, Bubbles, Paystack, transfers, and checkout retries do not cause duplicate charges or bookings.
- Test mobile calendar accessibility, date scoping, timezone grouping, pagination beyond 50/100 sessions, assigned Academy classes, cancelled Sessions and session media permission checks.
- Keep unfinished full-resource availability and arbitrary new-session purchasing behind explicit nonbookable states.
