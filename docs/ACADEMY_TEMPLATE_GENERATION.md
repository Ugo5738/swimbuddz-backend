# Academy template generation

Academy templates carry an explicit `cohort_id` and `cohort_fee_mode`.
Generation validates the cohort with Academy over HTTP before writing any
sessions, then copies the cohort, billing mode, per-student price, capacity and
pool to each generated class. Normal booking/payment rules remain unchanged:
`included` costs enrolled students zero; `paid_extra` uses the session price and
is confirmed after successful payment. A stored operating price alone never
turns an included class into a chargeable class.

## Existing templates

Deploy the Sessions migration head `a9b1c3d5e748` through the normal release
pipeline, then deploy the frontend. This merges the existing Academy context
(`f8a0b2c4d637`) and recurrence (`e7f9a1b3c425`) revisions without rewriting
either history. Edit each Academy template once to select its cohort and payment
arrangement. Old templates are not assigned a cohort or paid-extra policy by
guessing from their title or price. They can still be read or archived; generation
returns an actionable 422 until a cohort is assigned. No existing sessions,
bookings, attendance or payments are rewritten.

For Joshua's extra-class template, choose the September beginner cohort,
**Paid extra class — charge separately**, ₦15,000 per student and capacity 2.
The separately created September 20 class already exists; do not create it again
or record a payment unless funds have actually been verified.

The weeks-based generator starts from the next occurrence (next week when today
is the template's weekday). The generator also accepts a date range governed by
the saved recurrence, or specific dates for one-off classes. All three modes
preserve the cohort and billing arrangement. Event sessions need a concrete Event link;
generic Event templates without one are rejected before any Session is written.
