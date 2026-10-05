# Club Location Mobility

This document defines how SwimBuddz Club access behaves across locations, pods, regular weekly practices, extra practices, walk-ins, and permanent location changes.

## Product model

A member has one **home Club location** for a location-specific Club enrollment. That enrollment owns the member's quarter, included regular practices, and Pod eligibility.

A **Pod** is a training group inside one Club. Pod membership is not a substitute for Club enrollment.

The normal Club product is **one included regular practice per week**. A quarter may deliberately include several recurring SessionTemplates, but selecting two weekly 'plan_included' series means the member is buying both series and prepaid members will be reserved into both.

Club Session access modes remain:

- 'plan_included' — sold as part of the Club quarter. Prepaid members are automatically reserved into future included swims and pay ₦0 again. Transition members book individually and pay the session price.
- 'active_club' — optional extra practice outside the sold quarter. A prepaid member at that home Club may opt in for ₦0; transition members pay the session price.
- 'paid_addon' — separately charged extra practice. Prepaid and transition members both pay the session price.

## Visiting another Club

Visiting another location is **not** a home-Club transfer.

A Club Session or SessionTemplate may explicitly set:

- 'allows_visiting_club_members'
- 'visiting_club_fee' / 'visiting_club_fee_kobo'

The member must have an active dated, location-specific 'ClubEnrollment' at a *different* Club. The host Session must allow visiting Club members.

When allowed, access resolves as 'club_visit':

- 'plan_included' or 'active_club' host Session:
  - use the explicit visiting Club member rate when configured;
  - otherwise fall back to the host Session's normal Club/session price.
- 'paid_addon' host Session:
  - always charge the host Session's full paid-add-on price. A visitor discount never overrides a paid add-on.

The visit creates a normal SessionBooking. It does **not** change the member's home Club, home Pod, or quarter.

Pod-scoped host Sessions remain Pod-scoped. Enabling visitors does not let a member bypass a host Pod roster.

Community drop-ins and Guests remain separate admission audiences with their own independent rates and flags.

## Walk-ins

Admin walk-ins use the same server-authoritative access and pricing resolver as normal member booking.

For a visiting Club member, an Admin-created walk-in therefore records:

- 'booking_source = admin_walk_in'
- 'access_source = club_visit'
- the host's resolved visitor/add-on price

If money is not collected at the pool, the existing outstanding-session-fee flow is used. The member can later settle through the supported Bubbles / Paystack / Bank Transfer path according to current payment policy.

## Pod changes

A Pod transfer is only valid **inside the same Club**.

Cross-Club Pod transfers return a conflict and require a home-Club location change first. This keeps commercial enrollment and operational Pod membership consistent.

## Permanent home-Club location changes

Admin location-transfer APIs:

- GET /api/v1/clubs/admin/members/{member_id}/enrollments
- POST /api/v1/clubs/admin/enrollments/{source_enrollment_id}/location-transfer/preview
- POST /api/v1/clubs/admin/enrollments/{source_enrollment_id}/location-transfer

The Admin member profile exposes this workflow.

A transfer:

1. verifies the source enrollment is active;
2. requires a different active Club and a published target plan that covers the member's full remaining access period;
3. reuses an existing Club-ready assessment rather than asking the swimmer to repeat readiness;
4. checks target Club and optional Pod capacity;
5. records an immutable 'ClubEnrollmentTransfer' audit row;
6. ends the source enrollment at the transfer time;
7. creates the target enrollment;
8. removes the old Pod assignment and optionally assigns a target Pod;
9. leaves annual SwimBuddz Membership untouched.

Transfers are immediate. Until a future move date, use cross-location visits.

### Transition-per-session enrollment

A transition member has not prepaid a Club quarter, so Admin may transfer the home Club immediately without collecting another quarterly Club fee. The existing entitlement end date is preserved and future bookings use the target Club's per-session prices.

### Quarterly-prepaid enrollment

A prepaid quarter has already purchased specific Session inclusions. A mid-quarter home-Club move is therefore a financial event, not a simple location edit.

The transfer preview calculates:

- source remaining included swims/value;
- target remaining included swims/value;
- estimated value difference.

Execution is intentionally blocked until a separate financial reconciliation policy is selected and implemented. The system does not silently discard purchased swims, create a refund, issue Bubbles, or collect a price difference.

Until then, a prepaid member can continue to use cross-location visits without changing the home quarter.

## Creating a new Club location

Do not create ad-hoc Sessions to represent a new location.

For a new Club location:

1. create the Club with its operating area and default pool;
2. create its regular 'plan_included' SessionTemplate;
3. configure capacity, operational pricing, guest admission, Community drop-in admission, visiting Club member admission, volunteers, and transport defaults on the template;
4. create optional 'active_club' or 'paid_addon' templates for additional practice;
5. generate and review the quarter from the chosen included template(s);
6. publish the quarter only after the exact included Sessions and price are correct.

No location, weekday, or price is hardcoded into the mobility domain logic.