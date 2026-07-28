# Session and Content Cadence

All times in this policy are West Africa Time (`Africa/Lagos`).

## Weekly Session Digest

The weekly session digest runs on Sunday at 08:00 WAT and covers the following
Monday through Sunday using a half-open date window. It is a summary of sessions
that are already published; it is not the only way a session can be announced.

Keep the Sunday send. It gives members a predictable weekly planning point and
aligns cleanly with a Monday-to-Sunday activity week. Review the day only after
there is enough delivery, open, click, booking, and attendance data to compare
another cadence.

Community, Club, and Academy sections use lightweight default banner images
served by the frontend. Admins can replace each section image, description,
introduction, and gear guidance from Community > Content; a Media-library image
always overrides the packaged fallback.

The same audience image, introduction, gear guidance, pod/leader details,
weather, transport, fee, and capacity context powers individual booking prompts
and timed reminders. Available-session emails use the full image-led treatment.
The 24-hour reminder uses a compact image and preparation details; 3-hour and
1-hour reminders omit the large image and keep only last-mile operational facts.

Each recipient gets one combined email containing only the Community, Club,
and Academy sections they can attend. The shared backend access decision owns
that filtering, including cohort suspension and pod membership. A confirmed
booking remains in the digest even if the member's entitlement or pod assignment
changes later, so the member can still manage an existing commitment.

The digest includes current booking state, session fee and capacity, weather
when a pool forecast is available, configured transport, Pod Lead/coach details,
gear guidance, calendar links, and up to two tier-eligible recent articles.
Per-member dispatch state prevents a Sunday retry from sending the same campaign
twice. Admin reporting shows delivery outcomes, tracked clicks, and attributed
booking outcomes; ambiguous sends remain visible for review instead of being
retried automatically.

## Session Publication

Each session system has a different source and should not be forced into one
creation deadline:

- **Community:** publish the important sessions for the next Monday-to-Sunday
  week by Thursday at 09:00 WAT. This is the main weekly editorial deadline and
  gives members Friday and Saturday to plan before the Sunday digest.
- **Club:** maintain at least four weeks of published rolling coverage from
  recurring templates. Review and replenish the horizon by Thursday at 09:00
  WAT each week.
  Pod-scoped sessions remain visible only to the relevant pod.
- **Academy:** generate the full cohort schedule when the cohort is scheduled,
  and regenerate the extension window when a cohort is extended. Academy
  sessions are tied to enrollment and installment access, not a weekly manual
  publishing cycle.
  An unpaid installment remains in grace through the first day of the following
  month. It can suspend access from 00:00 WAT on the second day. The compliance
  job reverses premature `MISSED` states while that grace remains active.
- **Late changes:** publish or update them when confirmed. Immediate publication
  notifications and daily booking prompts cover changes made after the weekly
  digest cutoff.

The admin sessions screen reports next-week Community coverage, the Club
four-week horizon, Academy cohort sessions, drafts, and the next digest run.

## Booking Attendance

A confirmed booking creates or updates the member's attendance row to `present`
with the booking linked. This is the operational default, not a final coach
review: coaches and admins must overwrite no-shows, lateness, excused absences,
or cancellations. Repeating a confirmation repairs a failed attendance sync
without creating a second booking or attendance row.

The stale-attendance task reminds the assigned coaches and admins about default
or unreviewed rows after a session. The legacy no-show sweep only creates an
`absent` row when a confirmed booking has no attendance row at all; it does not
reverse the default-present policy.

## All-Bubbles Payments

Payment-intent session, bundle, and transport checkouts made entirely with
Bubbles still create a payment record. The member web booking flow uses this
path for every paid session; its direct sessions request is reserved for a
truly free session. After entitlement fulfillment succeeds, the payments worker
sends one admin receipt per configured admin email and records delivery state in
`payment_admin_email_logs`. Failed deliveries retry; completed payments leave
the queue, and historical payments are not backfilled into it.

## Offline Session Payments

An admin can settle an outstanding session booking from the attendance roster
when the member paid outside SwimBuddz checkout. The receipt records the
verified method (`bank_transfer`, `cash`, `pos`, or `other`), external
reference where applicable, receipt time, note, and recording admin. The
booking's server-side fee snapshot owns the amount; the endpoint does not
accept a client total or a partial payment.

The resulting `session_booking` payment follows the normal entitlement and
ledger path and is linked back to the booking. Existing legacy attendance rows
without a booking must first use **Create booking** so attendance, payment, and
session revenue remain separately auditable. Recording payment does not change
the member's attendance result.

## Article Publication

Use a human-reviewed weekly cadence:

1. Create or generate the draft by Monday.
2. Complete editorial and factual review by Tuesday.
3. Generate and approve the featured image on Tuesday.
4. Publish the article on Wednesday, normally at 07:00 WAT.
5. Include recently published, tier-eligible articles in Sunday's digest.

AI output is always an unpublished draft. An admin must review the title,
summary, body, tier, image, schedule, and `email_on_publish` decision before it
can reach members.

## Operational Checks

- A draft does not appear in member lists, public detail endpoints, or digests.
- A scheduled article publishes only when `scheduled_for` is due.
- Article recipients are snapshotted at publication and filtered by tier and
  email preference.
- Known email failures retry automatically; unknown provider outcomes do not,
  because retrying an ambiguous send could duplicate delivery.
- Existing published articles are not re-emailed when the durable dispatch
  migration is deployed.
