# Club Onboarding, Guest Passes, and Payment Charges

This document is the operating contract for the location-aware Club registration
and standalone guest-pass flows introduced in August 2026.

For tuition-included versus paid extra classes, historical bank receipts,
shared-cost calculations and archive/delete behavior, see
[Session extra classes and payment reconciliation](SESSION_EXTRA_CLASSES_AND_PAYMENT_RECONCILIATION.md).

## Club pricing and registration

Club pricing is configured as a versioned plan for one Club location. A plan
contains the Club fee, number of included sessions, refreshment inclusion, an
effective period, enforced capacity, an immutable area/pool snapshot, and the
optional quarterly Community Experience amount. The commercial plan is separate from pool and refreshment
cost-rate records: cost rates help an admin decide a price, while the plan is the
price a member is offered and later pays.

The quarterly Community Experience is optional and requires a linked
`CommunityExperienceOffering`. It can be selected by default when configured on
the plan, is displayed as its own line item, and can be unticked
before the application is submitted. The member's choice is saved on the
application and used by the server when pricing checkout. A client-supplied total
is never trusted.

The normal lifecycle is:

1. Admin associates a Club with its operating area/default pool and publishes a
   dated Club plan.
2. Member selects an operating area, pool location, the current open quarter
   (or first available quarter), and optionally a pod at that location. Future
   published quarters are independent optional prepayments: gaps are allowed,
   overlapping periods are rejected, and capacity is checked for each selection.
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

### Publishing plans when registration shows no locations

An existing Club, pool, or supplier cost rate does not itself create a commercial
Club plan. Use `/admin/club-plans` to publish the location's approved plan; the
member picker uses those active, effective plan versions. Configure the Club's
operating area and default pool first.

Use **Generate a quarter recommendation** for the first quarter or **Generate
next quarter** afterwards. The generator uses a real SessionTemplate, the Club's
home pool and weekly schedule, and fresh inherited pool/operating rates for each
date. Admin supplies/reuses expected attendance and margin using the normal
Session cost-plus calculation. No manually created source Session is required.

A full Saturday Q4 2026 has 13 actual swims, October 3–December 26. A recommended
₦65,000 quarter follows only when those thirteen Sessions are each priced at
₦5,000; twelve such swims total ₦60,000. These are illustrations, not defaults or
production constants. Different pools/dates can produce different prices.
Session count is derived from the selected real Sessions. Review exclusions,
the five-session entry minimum, costs and any deliberate final-price override.
Set the effective-from date to when members may apply, not necessarily quarter start.
Only publish locations intended to accept applications.

If offering the optional Q4 Experience, first create its matching-period offering
(₦50,000 standard, ₦40,000 Club member, ₦30,000 bundled) in
`/admin/community/experiences`,
then link it while publishing the Club plan. A plan with no linked offering
neither offers nor charges Experience. The obsolete fee-only checkout fallback
has been removed. Transition members may opt into the linked Experience at its
Standard member price (₦50,000 in this example), not the Club-later or bundle price.

The undeployed `e3b7a1c4d902` Q4 seed migration was removed from the review branch.
It must not be run; it used an incorrect hardcoded price/session count and no
Experience offering. Existing migration history is otherwise retained.

Proration uses actual eligible, not-yet-started Sessions and their frozen
commercial weights, including 13-session quarters; there is no calendar estimate
or universal twelve-session cap. `ClubPlanSession` records the purchased inclusion
and price snapshot, not another swim. Extra `active_club` practices do not increase
the quarter price/count. See [the scheduling contract](CLUB_EXPERIENCE_SCHEDULING.md)
for pod practices, rescheduling, Experience recovery and deployment checks.

### Member history and renewal

`GET /members/me/membership-history` powers Billing's Membership history and
Club renewal guidance. Dated Club enrollments use exact coverage periods;
legacy Membership records retain their known expiry and explicitly estimated
starts. A legacy Club period already represented by an exact enrollment is
suppressed, while separate older or adjacent periods remain visible. Cancelled
or revoked enrollments do not suppress valid legacy history.

Annual Membership and Club can expire independently. A former Club member uses
Billing's **Renew or rejoin Club** action; prior approved readiness can be reused
as described above. Both renewal cards open `/upgrade/club/plan`; only members
without reusable approval must complete readiness again. Active Club access
allows early renewal without relying on the legacy highest-paid-tier value.
A prepaid future quarter is shown as upcoming and does not
grant access before its start. Renewal dates follow continuous coverage, so a
later prepaid quarter does not hide an intervening gap.

## Club payment arrangements

`quarterly_prepaid` is the standard Club product. A settled application creates
one dated enrollment per selected quarter, and eligible sessions covered by
that enrollment have no second member session charge.

`transition_per_session` is a temporary, admin-approved payment arrangement for
the remainder of 2026. It is **not** a permanent weekly or monthly Club product,
is never exposed globally, and is available only when an assessor enables it on
the individual approved application. Admin sets the expiry (current policy
default: 31 December 2026), but does not set an applicant-specific session rate. Admin may approve only
quarterly prepaid, only the transition, or both; the member sees exactly those
options.

A transition activation charges no quarterly Club fee. It creates a proper
Club enrollment starting on activation and ending after the configured expiry
date, with application, Club, pool, operating area, payment mode, and optional
pod snapshots. If annual SwimBuddz Membership does not cover
the transition period, the same checkout adds the required ₦20,000 annual block.
If it already covers the period, the annual line is ₦0. A zero-total transition
activation is settled internally rather than sent to a payment provider.
Community Experience remains a distinct optional product in the same checkout.
The saved application choice is preserved, with a checkbox to opt in or out before
paying. Transition checkout uses the offering's **Standard member price**; buying
a quarterly Club plan uses its bundle price. Standalone Experience purchases by
transition-only members also use Standard pricing. No plan default is consulted
at checkout, and no linked, available offering means no Experience charge.

The member-facing wording is **Pay per swim until [approved expiry]**. Each
Club session's current Admin-configured `pool_fee` is resolved when the member
books or creates the payment intent. The booking/payment snapshots that resolved
amount, so a later session edit cannot rewrite an already-paid booking. Quarterly
Club enrollment becomes the standard from 2027.

Assessment results use the central branded email template, with coaching focus,
first goal and an authenticated next-step link. One approved option links directly
to Club checkout; multiple options link to Club plan selection. Academy-first
results link to Academy cohort selection. Internal coach notes are omitted from
emails and member-facing application responses. The stored outcome codes and
eligibility rules are unchanged.

Community-only members can display the same member-card QR from their profile.
It verifies identity/membership, not a paid booking or Club access; pool staff must
still check the session booking and payment. Zero-due transition checkout says
“Nothing due today” and “Activate Club access” without a payment-method selector.

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
| `club_transition` | active transition enrollment covers session date and pool/location (and pod when scoped) | session's current Admin-configured `pool_fee` |
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

### Product checkout discounts and Bubbles (September 2026)

Annual Membership, approved Club activation/quarters, Academy enrollment payments
and named Community Experience orders share the product checkout calculator.
The order is: server-owned price components → eligible discount → Bubbles →
processing charges on the remaining cash. Amounts are allocated in integer kobo.
The preview and payment intent use the same calculation. The optional reviewed
`expected_total_kobo` prevents an unnoticed price change between review and payment.

- Discount scopes are individual products: `COMMUNITY` (annual Membership),
  `CLUB`, `ACADEMY_COHORT`, `COMMUNITY_EXPERIENCE` and the explicit opt-in scope
  `COMMUNITY_EXPERIENCE_BUNDLE`. The historical `CLUB_BUNDLE` scope means Club and
  annual Membership, not Experience. An empty scope means regular-price items;
  it does **not** discount the already-reduced Club-bundle Experience price.
- A scoped discount only reduces its eligible components. A Club code cannot
  silently discount an annual Membership line, nor can an Academy code. Fixed
  discounts are capped to eligible value and allocated deterministically.
- Academy codes apply to the amount due **in this payment**, not automatically
  to all future installments. Frozen tuition, currency and Membership policy
  remain unchanged. A fully wallet-funded installment settles only the credited
  installment amount; zero cash does not clear future installment obligations.
- Transition enrollment still costs zero; annual Membership is included only
  when due. Experience is optional/explicit, at its **Standard member** price.
  A zero-due activation has no coupon/Bubbles controls or processing charge.
- Bubbles are tender, not a discount. A member can pay all or part of the net
  product price in whole Bubbles; fractions remain exact cash. Provider charges
  are not payable in Bubbles. Full-Bubbles/free checkout has no provider charge.
  Manual transfers cannot be combined with Bubbles. Public Experience guests
  may use an eligible code but cannot spend a member's wallet with an order token.
- Wallet holds are created before checkout, captured before entitlement, and
  captured idempotently on fulfillment retries. Failed capture leaves activation
  retryable rather than granting unpaid access. An uncertain provider response
  retains the frozen reference/hold: resume it, do not start a second payment.
  Member product requests accept a member-scoped `idempotency_key`; the member UI
  preserves it across retries. Experience uses its existing order reference.
  Already-started cash-only Experience orders remain cash-only and resumable.
- Payment metadata `checkout_quote` retains gross/net components, each discount
  allocation, Bubbles used, cash due and processing charges. Commercial/order
  amounts remain the original price/credit; this snapshot is the settlement
  authority. Member Billing receipts and activation/installment emails distinguish
  cash and Bubbles. Public metadata cannot forge product amounts or wallet capture.
- Cash ledger entries use only actual cash; wallet journals record Bubbles
  separately with the matching product source. No zero-value cash entry is emitted.
- Academy withdrawal policy credit is translated by Payments into the net cash
  and Bubbles actually refundable, excluding discounts, separate annual Membership
  and processing fees. Refunds for installments covered by one payment are grouped
  by reference. Admin's queue shows **cash** and **Bubbles** separately; marking
  disbursed returns whole Bubbles idempotently to the wallet. A fractional-Bubble
  remainder requires explicit reconciliation and blocks marking fully disbursed:
  it is never silently paid as cash, rounded away or rounded up. The existing
  best-effort Academy→Payments refund annotation remains an operational
  reconciliation boundary; failed annotations are logged, not a completed payout.

No migration or live price seeding is required for these modifiers. Discount
scopes and settlement snapshots use existing JSON columns. Unit tests exercise
the calculator, wallet guards, retry paths, refund allocation and frontend controls.
PostgreSQL concurrency/hold tests and live provider settlement still need CI or
staging verification; local Docker/database-backed execution was not performed.

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

## Attaching an offering after Club publication

Creating a Community Experience does not automatically attach it to every Club.
In **Admin → Club Pricing**, a published plan with no Experience now has an
**Attach optional Experience** action. It accepts an active same-quarter,
same-currency offering with a published, correctly bound future Event. It changes
no Club fee, Session inclusion, application selection or payment. The new option
starts unselected. Existing offering links cannot be replaced through this action,
so already-paid and pending checkouts keep their offering identity.

Quarterly checkout uses the linked offering's bundle price; transition checkout
uses its standard member price only when explicitly selected. An already-purchased
Experience, a closed purchase window or an unavailable Event still suppresses the
checkout option. Standalone Experience purchasing stays separate.
