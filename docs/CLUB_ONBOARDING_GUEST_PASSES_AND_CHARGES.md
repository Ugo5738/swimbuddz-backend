# Club Onboarding, Guest Passes, and Payment Charges

This document is the operating contract for the location-aware Club registration
and standalone guest-pass flows introduced in August 2026.

## Club pricing and registration

Club pricing is configured as a versioned plan for one Club location. A plan
contains the Club fee, number of included sessions, refreshment inclusion, an
effective period, enforced capacity, an immutable area/pool snapshot, and the
optional quarterly Community Experience amount. The commercial plan is separate from pool and refreshment
cost-rate records: cost rates help an admin decide a price, while the plan is the
price a member is offered and later pays.

The quarterly Community Experience is optional. It is selected by default when
the member chooses a plan, displayed as its own line item, and can be unticked
before the application is submitted. The member's choice is saved on the
application and used by the server when pricing checkout. A client-supplied total
is never trusted.

The normal lifecycle is:

1. Admin associates a Club with its operating area/default pool and publishes a
   dated Club plan.
2. Member selects an operating area, pool location, one or more consecutive
   Club quarters, and optionally a pod at that location.
3. Member completes the safety pre-assessment.
4. Admin records the observed 10–15 minute readiness assessment with one of:
   `club_ready`, `club_ready_modified`, or `academy_first`, and explicitly
   chooses the payment arrangements allowed for that application.
5. The result and baseline can be emailed to the member. Payment is available
   only for either Club-ready outcome.
6. Payments fetch the approved application price from members-service, reserve
   plan/pod capacity for 30 minutes, apply any enabled additional-charge
   policies, and persist the pricing snapshot.
7. Successful payment creates one dated, location-specific Club enrollment for
   every selected quarter and consumes the seat reservation. Pod joining is
   restricted to the member's enrolled Club location and pod capacity.

When a member later applies for a newly published Club commercial period, a
completed `club_ready` or `club_ready_modified` assessment from an enrolled
application can be copied with its source application ID. The new quarter is
still a deliberate purchase: readiness reuse never auto-enrolls or auto-charges
the member.

## Club payment arrangements

`quarterly_prepaid` is the standard Club product. A settled application creates
one dated enrollment per selected quarter, and eligible sessions covered by
that enrollment have no second member session charge.

`transition_per_session` is a temporary, admin-approved payment arrangement for
the remainder of 2026. It is **not** a permanent weekly or monthly Club product,
is never exposed globally, and is available only when an assessor enables it on
the individual approved application. Admin snapshots the member's session rate
and expiry (current policy default: 31 December 2026). Admin may approve only
quarterly prepaid, only the transition, or both; the member sees exactly those
options.

A transition activation charges no quarterly Club fee. It creates a proper
Club enrollment starting on activation and ending after the configured expiry
date, with application, Club, pool, operating area, payment mode, transition
rate, and optional pod snapshots. If annual SwimBuddz Membership does not cover
the transition period, the same checkout adds the required ₦20,000 annual block.
If it already covers the period, the annual line is ₦0. A zero-total transition
activation is settled internally rather than sent to a payment provider.

The member-facing wording is **2026 Club Transition — Pay Per Session**: Club
sessions are charged at the snapshotted rate when booked, and quarterly Club
enrollment becomes the standard from 2027.

Annual SwimBuddz Membership is a separate ecosystem product. A new swimmer does
not have to take an unrelated Community-first checkout path: if Membership will
not cover the selected Club period, the server adds the required renewal as a
separate line in the same approved Club quote. Successful settlement applies
that dated Membership entitlement before creating the Club enrollments. Legacy
Club checkout retains its existing one-year extension for transitional members;
new location-aware registrations must use an approved application ID.

Club and Academy are independent programmes, not ranks in a tier hierarchy.
Club access comes from a Club enrollment covering the session date and location;
Academy access comes from enrollment in the relevant cohort. Neither product
silently grants the other or annual Membership.

## Authoritative Club-session access and pricing

The server resolves the access source before it prices or reserves a Club
booking. Clients may display the returned label and amount, but a submitted
amount is never authoritative.

| Access source | Requirements | Authenticated member fee |
| --- | --- | ---: |
| `club_enrollment` | active prepaid enrollment covers session date and pool/location (and pod when scoped) | ₦0, included in Club quarter |
| `club_transition` | active transition enrollment covers session date and pool/location (and pod when scoped) | snapshotted transition rate |
| `community_dropin` | active annual Membership and the session explicitly enables Community drop-ins | session's `community_dropin_fee_kobo` |

All three paths consume normal session capacity. A Community-only member is
denied with a specific reason when `allows_community_dropins` is false. Session
admin exposes both that toggle and the independent Community rate.

Guest admission remains the separate GuestPass flow and uses
`guest_fee_kobo`. Guest and Community rates can currently be numerically equal
without becoming the same price source. Attached named guests on an
authenticated booking also use the guest rate, independently from the member's
resolved Club fee.

The access endpoint, direct booking, bundle reservation, attendance sign-in,
wallet/Bubbles debit, and payment-intent creation all consume the same resolved
member fee. Booking rows snapshot `access_source` and the member component for
audit. Pool, plan, pod, session-seat, and GuestPass hold capacity checks continue
to apply at their existing concurrency boundaries.

## Post-Academy Club bridge

Completing Academy does not imply generic Club access. A cohort may explicitly
set `post_graduation_club_bridge_months` from 0 to 12. Positive values cause the
graduation job to call the existing auditable bridge endpoint with an
enrollment-stable idempotency key; retries therefore do not stack extra months.
`0` and legacy `null` mean disabled and create no bridge.

The bridge is eligibility, not prepaid quarterly Club. A graduate with bridge
eligibility pays the prevailing operational Club session rate where the session
and location permit access; the bridge never manufactures a paid quarter.
Academy's `open`, `active_required`, and `included` annual-membership policies
remain unchanged.

## Additional payment charges

Admins can configure additional charges independently by payment purpose and
payment method. A policy can contain a percentage (basis points), a fixed amount,
an optional cap, an optional fixed-charge waiver threshold, and an active flag.
Purpose `*` applies to all payment purposes. A purpose-specific policy and a
matching `*` policy are cumulative, so do not use overlapping policies unless
that is intended.

Charges are calculated after discounts, shown separately in previews/checkout,
and snapshotted into payment metadata. Use a neutral label such as **Online
payment processing**. A Paystack processing charge is not VAT and must not be
presented as VAT. Disabling a policy stops it from being applied to new payments;
historical payment snapshots do not change.

No policy is seeded by the migration. Production charges remain off until an
admin creates and enables a policy.

## Guest and drop-in pricing

Every session can define three independent commercial prices:

- the normal member/session price;
- the Community-member drop-in price; and
- the public guest-pass price.

The guest and Community prices may happen to be equal during testing (for
example, NGN 7,000 in Yaba) but they are not linked. This allows a future guest
price such as NGN 10,000, or a different location price, without changing the
Community rate.

## Standalone self-paying guest pass

A guest can open `/guest-pass/session/{session_id}` without a member account,
enter required name/email/phone details, accept the safety waiver, and pay their
own guest price. The referrer does not need to book or attend. Referral links use
`?ref={member_referral_code}`.

An unpaid guest pass holds one session place for 30 minutes. Expired unpaid
holds no longer reduce availability. A delayed successful payment is confirmed
only if the session still has room.

Marketing consent is separate and defaults off. Transactional booking and
assessment emails do not depend on marketing consent. For a minor, guardian name
and phone are required by schema validation.

The public receipt endpoint is intentionally redacted: it exposes payment/status
information but no name, email, phone, safeguarding details, assessment, or
referrer information. The detailed record is admin-only.

After a paid guest attends, an admin records actual swim minutes and may record
and email an assessment. Guest minutes are retained as guest swimmer-hours and
can later be linked through `converted_member_id` when the guest becomes a
member. Once linked, the minutes move into that member's history and are excluded
from the aggregate guest bucket so total swimmer-hours count them once.

## Guest referral thank-you

A referred guest earns the configured referral thank-you (currently 10 Bubbles)
only after the first paid attendance for that normalized phone number. A unique
claim plus the rewards engine's idempotency key protects this under concurrent
attendance updates. Repeat visits do not earn another acquisition reward. The
existing member-conversion referral reward is a separate later event.

The guest flow validates the referral code with wallet-service and snapshots the
referrer's auth ID. Attendance emits `referral.guest_attended`; wallet-service
credits the 10 Bubbles automatically through its configured reward rule. Cash
transfers were a temporary pre-implementation workaround and are not the system
contract.

## Primary endpoints

- `GET /api/v1/clubs/plans`
- `GET /api/v1/pools/operating-areas`
- `GET /api/v1/pools?operating_area_id={id}`
- `POST /api/v1/clubs/applications`
- `PUT /api/v1/clubs/applications/{id}/pre-assessment`
- `GET /api/v1/clubs/admin/applications`
- `PUT /api/v1/clubs/admin/applications/{id}/assessment`
- `GET /api/v1/clubs/internal/applications/{id}/payment-context`
- `GET /api/v1/sessions/{id}/access`
- `GET|POST|PATCH /api/v1/payments/charges...`
- `GET /api/v1/sessions/{id}/guest-pass`
- `POST /api/v1/sessions/{id}/guest-passes`
- `GET /api/v1/guest-passes/{id}` (redacted public receipt)
- `GET|POST /api/v1/admin/guest-passes...`
