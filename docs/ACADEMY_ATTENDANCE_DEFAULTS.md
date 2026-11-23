# Academy attendance defaults

Owner-confirmed policy, 19 October 2026: enrolled Academy students default to
Present; coaches, administrators and students record exceptions. Students record
their own exceptions. This applies to every subsequent cohort, not a fixed list
of current students or cohorts.

Attendance's worker creates missing `present` / `swimmer` records for started,
included classes in active cohorts. A bounded recent-window pass (`lookback_hours=48`)
runs every 15 minutes and skips cohorts with no eligible started included class in
that window; a full active-cohort history repair (`lookback_hours=None`) runs at
worker startup and once daily (`02:15 UTC` / `03:15 WAT`) to heal any missing
earlier classes. It reads the
timetable and enrolments through their owning services and checks the existing
Members pre-swim clearance. It excludes future/draft/cancelled classes, classes
before enrolment, suspended students, and classes after a pause/drop date.
Optional paid extra classes retain their booking-based attendance.

Existing records always win, including Absent, Excused, Late and Cancelled. A
unique-constraint `ON CONFLICT DO NOTHING` also protects an exception recorded
between reconciliation's read and insert. The legacy no-show sweep skips
included Academy classes so missing records cannot become automatic absences.

The coach and admin sheets display the same default. Present is persisted
rather than represented only by the absence of an exception, so upload access,
attendance history, reporting and coach payout calculations use the same fact.
This process does not create a payment, booking, refund or reward transaction.

After a swim starts, a coach or admin reviews the roster and selects **Confirm
Present/Late swimmers for Bubbles**. Attendance counts only the confirmed
member swimmers. Ordinary saves and the default Present worker can still
record attendance. Correct an incorrect confirmation by saving the correct
status in the roster. Wallet accounts for any advance credit when calculating
the member’s next eligible award. See [ADR-019](DECISIONS.md#adr-019-one-bubble-per-confirmed-swim-day).

Students use **Update my Academy attendance** on their own class card in My
Swims. `POST /api/v1/attendance/sessions/{session_id}/my-academy-attendance`
derives the member identity from authentication and requires active enrolment
in that class's cohort; it requires no separate booking. The request accepts
only `status` (Present, Absent, Excused or Late) and an optional note. It cannot
edit another student's attendance. Student reports can be changed before the
class ends; historical corrections remain available to coaches/admins.
Excused absence retains the existing 24-hour notice rule; a shorter-notice
absence is recorded as Absent. Students can undo their own reports, but cannot
clear a saved coach/admin exception back to Present. Coach/admin corrections
remain authoritative and the automatic worker never overwrites any exception.

Preview in the Attendance container:

```python
from services.attendance_service.services.academy_defaults import reconcile_academy_defaults
await reconcile_academy_defaults(dry_run=True)
```

The same call without `dry_run=True` applies missing records. Inspect errors
before treating a cohort as reconciled; unavailable upstream services are not
treated as evidence of attendance. Repeating it cannot erase saved exceptions.
