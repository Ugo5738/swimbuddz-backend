# Calendar Activity Projection

The calendar is a discovery projection over domain-owned activities. It does
not own booking, RSVP, pricing, attendance, recurrence, or lifecycle rules.

## Ownership

- `events_service` owns Events, EventTemplates, RSVP, visibility, and event
  recurrence planning.
- `sessions_service` owns operational swims, capacity, booking, payment,
  attendance context, volunteer materialisation, ride-share, and SessionTemplate
  recurrence.
- `gateway_service` combines the read models and returns navigation links to the
  owning domain.

`source` and `activity_type` are open strings. Consumers must use the returned
`available_activity_types` metadata and provide a humanised fallback for keys
they have not seen before. Adding a future activity source must not require a
new universal Activity table or a closed client enum.

## Independent dimensions

Each calendar item keeps these concerns separate:

- `source`: owning domain, currently `session` or `event`.
- `activity_type`: the domain subtype, such as `club`, `cohort_class`,
  `community_swim`, `hangout`, or `open_swim`.
- `primary_audience`: the presentation lane and default colour.
- `audiences`: every relevant programme audience (`community`, `club`, and/or
  `academy`). Audience filters match any value in this list.
- `visibility`: who can discover the activity.
- `tier_access`: who can participate or book.
- `pricing`: commercial rules owned by the source domain.
- `occurrence`: concrete date/time; recurrence rules remain with the source
  domain.

Audience is not an access-control shortcut. A public Academy or Club activity
can be publicly discoverable even when attendance requires the corresponding
programme access.

The scalar Event `audience` field is deprecated but retained as a compatibility
mirror of `primary_audience` during the staged API migration. New clients write
`primary_audience` and `audiences`.

## Community activity mapping

- Official Community Swim: an Event with `activity_type=community_swim`, plus a
  concrete linked Session with `session_type=event` and `event_id`. Members book
  the Session so guest, payment, attendance, ride-share, volunteer, and media
  workflows have one operational owner.
- Quarterly/social hangout: Event-only unless it genuinely needs Session
  operations.
- Peer-organised open swim: Event with `activity_type=open_swim`; it is not an
  alias for an official Community Swim.

Event templates generate discoverable Event occurrences. Creating an
operational swim from an occurrence is explicit because the Session must retain
that concrete Event's `event_id`; EventTemplate and SessionTemplate are not
silently paired.

## Session recurrence and generation

Session templates support:

- weekly intervals;
- monthly, quarterly, and annual intervals;
- nth weekday rules such as the first Saturday of every month;
- day-of-month rules;
- optional start and end bounds.

Generation can use the persisted rule over a rolling number of weeks or an
inclusive date range. Admins can also provide one or more exact dates; exact
dates intentionally override the recurrence rule while retaining the template's
time, location, pricing, and operational configuration.

## Extension rules

When a new domain contributes calendar activities:

1. Keep lifecycle and actions in that domain.
2. Add a read adapter in the calendar projection.
3. Return a stable `source`, activity key/label metadata, and an owning-domain
   link.
4. Do not add a generic Activity write model or duplicate domain business
   rules in the Gateway.

Speculative integrations and automatic EventTemplate-to-SessionTemplate pairing
are intentionally deferred until a concrete workflow requires them.
