# Club Onboarding, Guest Passes, and Payment Charges

This document is the operating contract for the location-aware Club registration
and standalone guest-pass flows introduced in August 2026.

## Club pricing and registration

Club pricing is configured as a versioned plan for one Club location. A plan
contains the Club fee, number of included sessions, refreshment inclusion, an
effective period, capacity guidance, and the optional quarterly Community
Experience amount. The commercial plan is separate from pool and refreshment
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
2. Member selects the location plan and may select a pod at that location.
3. Member completes the safety pre-assessment.
4. Admin records the observed 10–15 minute readiness assessment with one of:
   `club_ready`, `club_ready_modified`, or `academy_first`.
5. The result and baseline can be emailed to the member. Payment is available
   only for either Club-ready outcome.
6. Payments fetch the approved application price from members-service, apply any
   enabled additional-charge policies, and persist the pricing snapshot.
7. Successful payment creates a location-specific Club enrollment. Pod joining
   is restricted to the member's enrolled Club location.

The annual Community membership remains a separate ecosystem membership. Both
the member UI and server-side payment context require it to be active before a
new location-plan payment. Location-plan activation does not silently extend the
annual Community expiry. Legacy Club checkout retains its existing one-year
extension for existing/transitional members; new location-aware registrations
should use an application ID.

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

Marketing consent is separate and defaults off. Transactional booking and
assessment emails do not depend on marketing consent. For a minor, guardian name
and phone are required by schema validation.

The public receipt endpoint is intentionally redacted: it exposes payment/status
information but no name, email, phone, safeguarding details, assessment, or
referrer information. The detailed record is admin-only.

After a paid guest attends, an admin records actual swim minutes and may record
and email an assessment. Guest minutes are retained as guest swimmer-hours and
can later be linked through `converted_member_id` when the guest becomes a member.

## Guest referral thank-you

A referred guest becomes eligible for the session's configured thank-you
(currently NGN 1,000 by default) only after the first paid attendance for that
phone number. Repeat visits do not earn another acquisition reward. The existing
member-conversion referral reward is a separate later event.

The guest flow validates the referral code with wallet-service and snapshots the
referrer's auth ID. The first version deliberately records eligibility and a
manual transfer reference rather than initiating an automatic payout. Admin
should verify the referral, make the transfer, then mark it paid. This provides an
audit trail while the programme is still being tested.

## Primary endpoints

- `GET /api/v1/clubs/plans`
- `POST /api/v1/clubs/applications`
- `PUT /api/v1/clubs/applications/{id}/pre-assessment`
- `GET /api/v1/clubs/admin/applications`
- `PUT /api/v1/clubs/admin/applications/{id}/assessment`
- `GET|POST|PATCH /api/v1/payments/charges...`
- `GET /api/v1/sessions/{id}/guest-pass`
- `POST /api/v1/sessions/{id}/guest-passes`
- `GET /api/v1/guest-passes/{id}` (redacted public receipt)
- `GET|POST /api/v1/admin/guest-passes...`
