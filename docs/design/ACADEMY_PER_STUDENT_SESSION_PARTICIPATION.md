# Academy student-specific session participation (implementation contract)

## Purpose

A single physical cohort session must support different commercial and academic
treatments for different enrolled learners. Example: October 24, 2026,
14:00–16:00 at Oduduwa House Pool has two attendees: student A attends using
one of their existing twelve purchased lessons; student B purchases an
additional class without using one of their purchased lessons.

The existing `Session.cohort_fee_mode` is a cohort-wide default. It must no
longer be the sole price/entitlement authority once explicit learner-specific
allocations are enabled. **Do not create two sessions.**

## Ownership and required write path

- Sessions service owns one Session, session capacity, the price snapshot on
  each SessionBooking, and booking/payment-link linkage.
- Academy service owns enrollment identity, the immutable purchased-class
  allowance (which may differ from `Program.duration_weeks`), class-consumption
  ledger, approved support and make-up credits, and remaining-class balance.
- Attendance service owns evidence of participation/absence/late/excused.
- Payments service owns receipts, verified credits, outstanding charges,
  refunds and reconciliation. Never mark paid on attendance alone.

An admin command must be idempotent per (enrollment, session), require an
authenticated privileged actor and server-verified enrollment/cohort/member
identity, and persist one learner participation: `prepaid`, `paid_extra`,
`makeup`, or `support`.

Use a versioned orchestration process to prevent a partially written
entitlement/booking/payment record from being portrayed as complete. On
failure, expose a reconcilable state and safe retry. Do not attempt distributed
database commits or silent financial correction.

## Per-student cases

| kind | owed kobo | purchased-class balance | extra credit |
| --- | ---: | --- | --- |
| prepaid | 0 | consume one (once) | unchanged |
| paid_extra | explicitly agreed positive amount | unchanged | unchanged |
| makeup | 0 | unchanged | consume one approved makeup |
| support | 0 | unchanged | no credit, explicit approval required |

Future planned reservations must not silently consume delivered-class credits;
track reserved vs delivered quantities separately. No retrospective change to
paid/confirmed booking price is allowed without audited correction; paid
booking amount and payment allocation must reconcile.

Existing cohort attendance default-present rules are **not** in themselves
enough to establish purchased-class consumption; reconcile historical regular
classes by real attendance and the applied absence/make-up policy, preserving
the enrollment timeline and cohort-transfer history. Never silently default
existing paid learners to zero previous classes or to twelve free new classes.

## UI flow

Create the session once at Admin > Sessions using the actual pool, start/end
and capacity. In its roster, choose a participation kind independently for
each student, display purchased/used/reserved/remaining entitlement from
Academy, and show the agreed paid-extra fee independently. Require a visible
reason for support; label makeups from an approved credit. Only allow checkout
or bank reconciliation for a charge owed, and use the member booking's
authoritative price snapshot.

Show a per-member confirmation, booking status, attendance status, entitlement
change and payment state. For past sessions, allow reviewed retrospective
allocation with original attendance timestamp and actor audit.

## Release gate / acceptance

1. Existing routine Sunday lesson consumes exactly one purchased class on
   attendance; no new payment.
2. Same Saturday physical session: learner A prepaid consumes one purchased
   class and is charged zero; learner B paid-extra owes the negotiated fee and
   consumes no purchased class.
3. Same paid-extra session can include A at zero and B at a paid price.
4. Duplicate, concurrent or retried saves do not double-book, double-consume,
   double-credit or record duplicate payments.
5. Missing, wrong-cohort or inactive enrollment cannot use tuition.
6. Exhausted prepaid balance is rejected without charge or booking mutation;
   approved support/makeup remain separately available.
7. Unpaid booked extras show outstanding, not paid; verified receipts match
   exactly once to their original payment record. An extra can be attended
   before settlement without incorrectly setting payment status paid.
8. Absent/excused cancellations, refunds, class reversals and cohort transfers
   preserve audit history and restore/retain balances according to policy.
9. Existing Academy paid installments, milestones, video evidence and
   certificates are unaffected by attendance-only classification.
10. Member-facing My Swims, coach attendance, media vault and reports point to
    the one physical Session.
11. Backfilled historical consumption is deterministic and auditable, with
    manual review of ambiguous attendance rather than guessing.
12. Run both backend and frontend tests, migration up/down and Postgres
    integration with concurrency; check generated OpenAPI and TS types.
13. Feature is disabled until both services deploy compatibly and the
    historical reconciliation is signed off.

**Do not treat the pure classification rules as the implementation.** The first-release scope and remaining integration boundaries are set out below.


## Implementation boundary for first release (October 2026)

The first integrated admin control supports the two directly requested classifications:
`prepaid` (one purchased class, no additional charge) and `paid_extra`
(separately agreed positive fee, no prepaid deduction). It works on one
physical Academy cohort session and stores one enrollment/session allocation.
Paid extras remain unpaid until normal verified payment fulfillment; attendance
is not a receipt. Routine included cohort classes after the verified opening
cutoff are counted from actual present/late attendance and excluded if an
explicit allocation already exists. Missing and absent/excused attendance
remains flagged as unverified; further prepaid consumption fails closed until
reviewed, rather than creating a new entitlement by inference.

Existing make-up bookings must continue through their already implemented
make-up and payout workflows. Support-class approvals likewise remain under
existing processes. Although the allocation schema reserves identifiers for
`makeup` and `support`, the integrated admin booking route **rejects**
those types; they are not implemented as a new universal credit ledger. A
separate integration is required before claiming that all four sources share
the new booking UI.

Release operations: the Academy administrator must verify one historical
opening balance per enrollment, with a time cutoff before the target session.
The front-end then uses Admin Attendance to classify each participant; enter
the true per-student fee, do not represent money already received as a discount.
Do not backdate an opening balance to make unresolved regular attendance
disappear. Deploy compatible sessions/academy services and frontend together
with migrations first; test a real staging cohort before production.

This document describes the full design target above and the narrower,
explicit first-release behavior here. Approval of this first release does
not imply completion of every future make-up/support/reversal requirement.
