# Guest booking implementation and review guide

Updated 24 September 2026. This implementation extends the existing standalone
GuestPass domain. BookingGuest remains the guest paid for on a member's booking.

## Admission and invitation boundaries

| Session setting | Guest admission |
| --- | --- |
| `disabled` | No new standalone guest self-booking |
| `public` | Public link; inviter attribution is optional |
| `member_invite` | Signed session invitation from a confirmed member booking, or individual admin approval |
| `approval_required` | Individual admin approval only |

An explicit nonnegative `guest_fee_kobo` is required, including an intentional zero.
Member fees never supply a fallback. Existing explicitly priced Club/Community
sessions migrate to public; Academy/Event sessions and unpriced sessions migrate
to disabled. New sessions default to disabled.

`ref` only attributes a referral. `source` and `campaign` describe acquisition and
never authorize access. A member invitation is a domain-separated HMAC over the
Session ID and confirmed SessionBooking ID. It travels in `#invite=...`, then an
`X-Guest-Invite-Token` header for the offer and `invite_token` for checkout. The
backend checks the booking still belongs to that Session and is confirmed on
both offer and checkout. A reusable referral code, another Session's token, an
unbooked member, or a cancelled inviter booking cannot unlock member-invite mode.
Public sessions deliberately permit sharing by members who have not booked.

Individual admin approvals are email-bound, single-use, expiring (72 hours by
default) and revocable. Only their hashes are stored. They travel in `#token=...`
and `X-Guest-Booking-Token` / `access_token`. Private or invite-only Event policy
still requires individual approval. Event policy lookup fails closed.

## Reservation, settlement and recovery

- Before `guest_booking_closes_at` (or the start): reserve one space for 30 minutes.
  Member and standalone guest reservations lock the Session and share capacity.
- From the start through `ends_at + guest_reconciliation_days`: settlement on the
  same URL, without a capacity hold. Default 3 days; zero requires admin approval.
- After that: an individual admin link can allow historical reconciliation.
- Payment never records guest attendance. Admin attendance controls the existing
  first-paid-attendance referral reward; retries do not grant it again.
- Admin can restore payment on an existing pending/failed pass. Before the start,
  this checks capacity and renews the hold; afterward it switches to settlement.
  The pass, identity, frozen price, method and payment reference stay the same.
- Paystack and manual transfer use the existing Payments initializer and receipt
  upload workflow. A transfer receipt alone does not confirm payment or renew a
  hold. Resumed guest transfers update deadline metadata without repricing.

The safety checkbox records `pool-safety-2026-09` and acceptance time. It is a
safety acknowledgement, not a newly drafted legal release. Marketing consent is
separate. Legacy acknowledgements retain a null version rather than being relabelled.

## Email, privacy and operations

A deterministic outbox key and row lock serialize repeated/concurrent payment
callbacks. Confirmation and enqueue are in one transaction. Paystack, approved
transfers, free/Bubbles and bundle member bookings use the same branded
confirmation delivery. Actual payment totals are snapshotted for retries. Bundle
allocation preserves integer cash/Bubbles totals. Guest confirmations and
assessment messages use branded Communications templates. Historical confirmations
avoid future check-in/packing instructions.

The Sessions worker retries due unsent confirmations every five minutes. Missing
optional invitation enrichment (Wallet/Events outage) is logged and the booking
confirmation is sent without it. Email delivery is acknowledged by Communications;
this is not proof of inbox delivery. As with an SMTP outbox, a process crash after
provider acceptance but before marking the row sent is an ambiguous delivery
window; callback idempotency does not imply provider-level exactly-once delivery.
No real provider payments or outbound emails were made during this implementation.

The offer redacts a private venue. A separate, domain-separated receipt capability
in the URL fragment / `X-Guest-Pass-Token` unlocks venue details after confirmation
and authorizes retry. Public receipt IDs reveal no guest identity, assessment or
private location. Exact receipt links should be treated as private.

All swimmers combines members, member-booking guests and standalone guests. It
shows confirmed/attended GuestPass rows and active pending reservations, excluding
failed, expired and unpaid settlement attempts. Guest Passes retains all attempts
for payment follow-up. The funnel counts views/shares as anonymous interactions,
not unique people; checkout onward counts guest passes. Existing conversion and
swim-hour accounting remain in use.

## Surfaces and API additions

Frontend: booking success, existing-booking view, confirmation email, Session
editor, attendance page, linked Event sessions, Guest Passes, public checkout and
private receipt. Copy, native share, WhatsApp and QR are available. Admin navigation
already had Guest Passes and continues to do so.

Gateway prefix is `/api/v1`:

| Method/path | Purpose |
| --- | --- |
| `GET /sessions/{id}/guest-pass` | Offer, policy, lifecycle, capability validation |
| `POST /sessions/{id}/guest-passes` | Create independent guest checkout |
| `GET /guest-passes/{id}` | Redacted or private receipt |
| `POST /guest-passes/{id}/checkout` | Capability-authorized payment retry |
| `GET /sessions/{id}/guest-share-link` | Authenticated member's eligible link |
| `GET /admin/sessions/{id}/guest-share-link` | Admin attribution for an eligible inviter |
| `POST /admin/sessions/{id}/guest-booking-links` | Individual approval/reconciliation |
| `DELETE /admin/guest-booking-links/{id}` | Revoke individual approval |
| `POST /admin/guest-passes/{id}/payment-link` | Restore an existing guest payment |
| `GET /admin/sessions/guest-booking-options` | All eligible types, paged history |
| `GET /admin/sessions/{id}/roster` | Combined operational roster |
| `POST /sessions/{id}/guest-link-events` | Anonymous view/share events |
| `GET /admin/guest-passes/funnel` | Session/source/campaign funnel filters |

New creation/retry routes are rate limited and capability headers are allowed by
CORS. OpenAPI and frontend generated types are regenerated. Browser member
confirmation reads server-fulfilled status; it cannot mark an unpaid booking paid
by submitting an arbitrary payment ID.

## Deployment and migration order

At the checked-out refs reviewed on 24 September, frontend `origin/main` contained
the guest UI while backend `origin/main` lacked the implementation (backend work
was on `develop`). This is repository evidence, not a claim that live production
was queried. These changes have not been deployed by this task.

Deploy as a coordinated backend/frontend release:

1. Apply Sessions migrations `dac9b92b1158`, `0437991503dd`, `bd99897b8e40` in order.
   Check for legacy negative guest fees before adding the nonnegative constraint;
   resolve any such configuration deliberately. Do not silently reprice it.
2. Release Communications (new templates), Events (privacy contract), Sessions
   API and worker, Payments (confirmation snapshots/recovery metadata) and Gateway
   together. Ensure the Sessions ARQ worker runs the new retry task.
3. Release the matching frontend. It fails closed if an old offer omits admission
   policy; member-invite URLs need the updated fragment-token handling.
4. In staging, enable intentional guest prices/policies for representative
   Community, Club and Academy sessions; verify one Paystack sandbox and one
   admin-approved manual transfer plus real branded-email delivery.

Do not blindly replay all historical confirmations: legacy `sent_at` values were
not reliably recorded. This implementation queues current fulfillment and retries
its own durable outbox; a historical resend needs a separately selected scope.

## Validation and reviewer entry points

Backend tests:

- `test_guest_booking_policy.py`: lifecycle/cutoffs, private Event policy, receipt
  capability, safety and guardian validation.
- `test_guest_booking_database.py`: explicitly isolated PostgreSQL; concurrent
  guest/member capacity, repeated callbacks, free/settlement/transfer paths,
  approval expiry/revocation, recovery, private receipts, invitation scope and
  cancelled inviter, optional enrichment outages, roster/funnel, HTTP auth.
- `test_guest_gateway_routes.py`: routing, capability forwarding and CORS.
- `test_guest_confirmation_templates.py`, `test_direct_session_booking_email.py`:
  branding, historical copy, conditional invitation and exact bundle allocation.
- Existing pricing, entitlement, bundle, guest-hold, Event privacy, referral-link
  and manual-payment regression suites also run.

Run database tests only with `GUEST_TEST_DATABASE_URL` pointing to a migrated,
disposable **localhost** PostgreSQL database. They skip without it and reject a
remote host. Root pytest configuration loads `.env.dev`; do not use its default
shared database for these tests. External payments/email/member/reward calls are
mocked. Alembic upgrade → downgrade → upgrade, backfill defaults, checks and
`alembic check` were verified against the disposable database.

Final focused validation: 153 backend tests and 30 frontend tests passed; TypeScript,
production build and lint completed (existing repository lint warnings remain).

Frontend coverage includes lifecycle, free pricing, transfer consent/attribution,
member-invite access independent of referral, older-backend rejection and header
refetch behavior. TypeScript, lint and production build are checked. Local browser
QA completed a free guest checkout into its private receipt, revealing the venue
only after confirmation, and inspected the post-session screen. Google font
fetching required network access for the production build.
