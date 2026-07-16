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
- **Late changes:** publish or update them when confirmed. Immediate publication
  notifications and daily booking prompts cover changes made after the weekly
  digest cutoff.

The admin sessions screen reports next-week Community coverage, the Club
four-week horizon, Academy cohort sessions, drafts, and the next digest run.

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
