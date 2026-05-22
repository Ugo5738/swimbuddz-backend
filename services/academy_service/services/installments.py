"""Installment planning and payment-state helpers for academy enrollments."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from libs.common.datetime_utils import utc_now
from services.academy_service.models import (
    Enrollment,
    EnrollmentStatus,
    InstallmentStatus,
    PaymentStatus,
)

FOUR_WEEK_BLOCK_WEEKS = 4
THREE_INSTALLMENT_CAP_THRESHOLD_KOBO = 150_000 * 100
MAX_INSTALLMENTS_OVER_CAP = 3
WAT_TZ = ZoneInfo("Africa/Lagos")

# Founder-confirmed policy (May 2026): when a custom deposit amount is used for
# installment plans, it must be at least one-third of the total program fee.
# This prevents thinly-funded deposits that leave the member exposed to large
# late-cycle balances and protects the school's cash flow.
MIN_DEPOSIT_RATIO = 1 / 3

# Payment window: installment is MISSED only after this many hours past due Monday 00:00 WAT.
# This gives students until Monday 23:59 WAT to pay before the miss is recorded.
GRACE_HOURS = 24


def validate_duration_weeks(duration_weeks: int) -> None:
    if duration_weeks <= 0:
        raise ValueError("Program duration must be greater than 0 weeks")
    if duration_weeks % FOUR_WEEK_BLOCK_WEEKS != 0:
        raise ValueError("Program duration must be a multiple of 4 weeks")


def block_count_for_duration(duration_weeks: int) -> int:
    validate_duration_weeks(duration_weeks)
    return duration_weeks // FOUR_WEEK_BLOCK_WEEKS


def installment_count(total_fee: int, duration_weeks: int) -> int:
    blocks = block_count_for_duration(duration_weeks)
    if total_fee > THREE_INSTALLMENT_CAP_THRESHOLD_KOBO:
        return min(blocks, MAX_INSTALLMENTS_OVER_CAP)
    return blocks


def split_amounts(total_fee: int, count: int) -> list[int]:
    if total_fee < 0:
        raise ValueError("Total fee cannot be negative")
    if count <= 0:
        raise ValueError("Installment count must be greater than 0")

    base = total_fee // count
    remainder = total_fee - (base * count)
    amounts = [base for _ in range(count)]
    # Remainder goes to first installment (larger first payment, better cash flow)
    amounts[0] = base + remainder
    return amounts


def monday_00_wat(dt: datetime) -> datetime:
    wat_dt = dt.astimezone(WAT_TZ)
    monday_date = wat_dt.date() - timedelta(days=wat_dt.weekday())
    return datetime.combine(monday_date, time.min, tzinfo=WAT_TZ)


def build_schedule(
    *,
    total_fee: int,
    duration_weeks: int,
    cohort_start: datetime,
    enrolled_at: datetime | None = None,
    count_override: int | None = None,
    deposit_override: int | None = None,
) -> list[dict]:
    """
    Build the installment schedule for an enrollment.

    - ``enrolled_at``: when the student actually enrolled. The schedule is
      anchored to ``max(cohort_start, enrolled_at)`` so mid-cohort joiners
      don't receive back-dated installments (the bug that previously caused
      DROPOUT_PENDING within minutes of registration). Default ``None`` keeps
      back-compat with callers that don't need late-join awareness.
    - ``count_override``: admin-set total number of installments; defaults to
      ``installment_count(total_fee, duration_weeks)``.
    - ``deposit_override``: admin-set first-installment amount (kobo); if set,
      the remainder is split evenly across the remaining installments. Defaults
      to auto even-split via ``split_amounts``.

    Amounts are returned in kobo (minor NGN unit) for internal consistency.
    """
    count = (
        count_override
        if count_override and count_override >= 2
        else installment_count(total_fee, duration_weeks)
    )

    if deposit_override is not None and deposit_override > 0 and count >= 2:
        # Enforce the 1/3 floor on custom deposits (founder policy May 2026).
        # Use ceil-ish via integer math: deposit * 3 >= total_fee.
        if deposit_override * 3 < total_fee:
            min_kobo = (total_fee + 2) // 3  # round up
            raise ValueError(
                f"Custom deposit too small: NGN {deposit_override / 100:.2f} "
                f"is less than 1/3 of the program fee "
                f"(minimum NGN {min_kobo / 100:.2f})."
            )
        # Admin set a specific deposit; split remaining fee evenly
        remaining = total_fee - deposit_override
        subsequent_base = remaining // (count - 1)
        subsequent_remainder = remaining - subsequent_base * (count - 1)
        amounts = [deposit_override] + [subsequent_base] * (count - 1)
        # Spread any leftover kobo into the second installment
        if subsequent_remainder > 0:
            amounts[1] += subsequent_remainder
    else:
        amounts = split_amounts(total_fee, count)

    cohort_anchor = monday_00_wat(cohort_start)
    if enrolled_at is not None:
        # Anchor to the later of cohort start and enrollment date.
        # max() works on tz-aware datetimes; both anchors are WAT here.
        anchor_wat = max(cohort_anchor, monday_00_wat(enrolled_at))
    else:
        anchor_wat = cohort_anchor

    schedule: list[dict] = []
    for idx, amount_kobo in enumerate(amounts, start=1):
        due_wat = anchor_wat + timedelta(weeks=(idx - 1) * FOUR_WEEK_BLOCK_WEEKS)
        schedule.append(
            {
                "installment_number": idx,
                "amount": amount_kobo,
                "due_at": due_wat.astimezone(ZoneInfo("UTC")),
            }
        )
    return schedule


def current_block_number(
    *,
    now: datetime,
    cohort_start: datetime,
    duration_weeks: int,
) -> int:
    blocks = block_count_for_duration(duration_weeks)
    now_wat = now.astimezone(WAT_TZ)
    start_wat = cohort_start.astimezone(WAT_TZ)
    if now_wat <= start_wat:
        return 1

    elapsed_days = (now_wat - start_wat).days
    current = (elapsed_days // (FOUR_WEEK_BLOCK_WEEKS * 7)) + 1
    return max(1, min(current, blocks))


def apply_member_payment_across_installments(
    *,
    amount_kobo: int,
    installments: list,
    now: datetime,
    payment_reference: str | None = None,
) -> tuple[list, int]:
    """Apply a member-initiated payment across one or more installments.

    Founder-confirmed policy (May 2026): a member can pay manually at any
    time. The minimum is the stipulated amount of the next unpaid installment;
    the maximum is the full remaining balance. Custom amounts between roll
    forward through subsequent installments — marking each one PAID in turn
    until the amount is consumed. Any remainder that does not cover the next
    installment's full amount is applied as a *reduction* to that installment,
    so the member sees a smaller amount due next time.

    Mutates the installments in place. Returns ``(modified_installments,
    overshoot_kobo)`` — overshoot is the amount the caller asked to apply
    beyond the total remaining balance (should be 0 if the caller validated).
    """
    paid_statuses = {InstallmentStatus.PAID, InstallmentStatus.WAIVED}
    pending = [
        i
        for i in sorted(installments, key=lambda i: i.installment_number)
        if i.status not in paid_statuses
    ]

    remaining = amount_kobo
    modified: list = []
    for inst in pending:
        if remaining <= 0:
            break
        if remaining >= inst.amount:
            # Pay this installment in full and move on
            inst.status = InstallmentStatus.PAID
            inst.paid_at = now
            if payment_reference is not None:
                inst.payment_reference = payment_reference
            remaining -= inst.amount
            modified.append(inst)
        else:
            # Partial: reduce this installment's stipulated amount by the
            # leftover so the next payment is smaller. The installment stays
            # PENDING — the member hasn't fully covered it yet.
            inst.amount -= remaining
            remaining = 0
            modified.append(inst)
            break

    return modified, remaining


def compute_withdrawal_refund(
    *,
    now: datetime,
    cohort_start: datetime,
    duration_weeks: int,
    mid_entry_cutoff_week: int,
    total_paid_kobo: int,
    program_fee_kobo: int,
) -> tuple[str, int, float]:
    """Compute refund amount per the SwimBuddz withdrawal policy.

    Policy (per founder, May 2026):
      - Before cohort starts: 90% of what was paid (10% admin fee).
      - Week 1 → mid_entry_cutoff_week: 50% of the unused prorated portion,
        capped at what was paid.
      - After mid_entry_cutoff_week: no refund.
    Remaining unpaid installments are waived in all cases (handled by caller).

    Returns ``(window, refund_kobo, refund_percent_of_paid)`` where ``window``
    is one of ``"before_start" | "mid_entry_window" | "after_cutoff"``.
    """
    if total_paid_kobo <= 0 or program_fee_kobo <= 0:
        return (
            _classify_window(
                now=now,
                cohort_start=cohort_start,
                duration_weeks=duration_weeks,
                mid_entry_cutoff_week=mid_entry_cutoff_week,
            ),
            0,
            0.0,
        )

    window = _classify_window(
        now=now,
        cohort_start=cohort_start,
        duration_weeks=duration_weeks,
        mid_entry_cutoff_week=mid_entry_cutoff_week,
    )

    if window == "before_start":
        refund = (total_paid_kobo * 9) // 10  # 90% refund
    elif window == "mid_entry_window":
        # 50% of the unused-by-time portion of the FULL program fee,
        # capped at what was actually paid.
        elapsed_days = max(0, (now - cohort_start).days)
        total_days = duration_weeks * 7
        elapsed_frac = min(1.0, elapsed_days / total_days) if total_days else 1.0
        unused_kobo = int(round(program_fee_kobo * (1 - elapsed_frac)))
        refund = min(unused_kobo // 2, total_paid_kobo)
    else:
        refund = 0

    percent = (refund / total_paid_kobo) if total_paid_kobo else 0.0
    return window, refund, percent


def _classify_window(
    *,
    now: datetime,
    cohort_start: datetime,
    duration_weeks: int,
    mid_entry_cutoff_week: int,
) -> str:
    if now < cohort_start:
        return "before_start"
    elapsed_days = (now - cohort_start).days
    elapsed_weeks = elapsed_days // 7 + 1  # week 1 = first 7 days, etc.
    if elapsed_weeks <= max(1, mid_entry_cutoff_week):
        return "mid_entry_window"
    return "after_cutoff"


def mark_overdue_installments(
    installments: list,
    *,
    now: datetime,
) -> int:
    """Mark PENDING installments as MISSED if the 24h grace window has closed.

    Due date is Monday 00:00 WAT. Students have until Monday 23:59 WAT (24h) to pay.
    An installment is only counted MISSED after the grace window expires.
    """
    changed = 0
    grace_cutoff = timedelta(hours=GRACE_HOURS)
    for installment in installments:
        if (
            installment.status == InstallmentStatus.PENDING
            and installment.due_at + grace_cutoff <= now
        ):
            installment.status = InstallmentStatus.MISSED
            changed += 1
    return changed


def sync_enrollment_installment_state(
    *,
    enrollment: Enrollment,
    installments: list,
    duration_weeks: int,
    cohort_start: datetime,
    cohort_requires_approval: bool,
    admin_dropout_approval: bool = False,
    now: datetime | None = None,
) -> None:
    """Recalculate and apply enrollment status based on current installment state.

    Key behavioral rules (founder-confirmed May 2026):
    - ``missed_installments_count`` is LIVE — it reflects how many installments
      currently have status MISSED. Paying a late installment flips that
      installment to PAID and decreases the counter, giving the student a real
      path back from a late payment. (Previous behavior was cumulative —
      counter only ever went up — which created permanent DROPOUT_PENDING
      states from a single late payment.)
    - An installment is "due" when its ``due_at + GRACE`` is in the past.
      Compliance is driven by per-installment ``due_at``, NOT by block index.
      This decouples cash-flow cadence from cohort calendar — mid-cohort
      joiners whose installments are anchored to their enrollment date no
      longer trip the suspension check just because the calendar advanced.
    - At currently-missed >= 2:
        - If ``admin_dropout_approval`` is True on the cohort:
          move to DROPOUT_PENDING (admin must confirm).
        - Otherwise: move directly to DROPPED.
    - Suspension triggers when a required (past-due) installment is unpaid.
    - Paying a late installment lifts suspension immediately.
    - DROPOUT_PENDING auto-reverts to ENROLLED if the student catches up
      (missed_count drops below 2). DROPPED is final — admin-only reversal.
    """
    effective_now = now or utc_now()
    grace = timedelta(hours=GRACE_HOURS)
    total = len(installments)
    paid_statuses = {InstallmentStatus.PAID, InstallmentStatus.WAIVED}
    paid_count = sum(1 for i in installments if i.status in paid_statuses)
    missed_count = sum(1 for i in installments if i.status == InstallmentStatus.MISSED)

    enrollment.total_installments = total
    enrollment.paid_installments_count = paid_count
    # Live counter — reflects the present, not the cumulative history.
    enrollment.missed_installments_count = missed_count

    if enrollment.status == EnrollmentStatus.WAITLIST:
        enrollment.access_suspended = False
        enrollment.payment_status = (
            PaymentStatus.PAID if paid_count > 0 else PaymentStatus.PENDING
        )
        return

    # Due-date-based compliance: an installment is "required" only if its own
    # due_at + grace window has passed. Ignores block index entirely so a
    # student whose installment is due next month isn't flagged because the
    # cohort calendar's block boundary has rolled over.
    is_required_installment_unpaid = any(
        i.due_at + grace <= effective_now and i.status not in paid_statuses
        for i in installments
    )

    if missed_count >= 2:
        # Do not re-trigger if already fully dropped (e.g. admin already confirmed)
        if enrollment.status not in (
            EnrollmentStatus.DROPPED,
            EnrollmentStatus.DROPOUT_PENDING,
        ):
            if admin_dropout_approval:
                enrollment.status = EnrollmentStatus.DROPOUT_PENDING
            else:
                enrollment.status = EnrollmentStatus.DROPPED
            # Stamp the drop time on first transition so coach payout
            # calculations know when to stop counting eligible sessions.
            if enrollment.dropped_at is None:
                enrollment.dropped_at = effective_now

        enrollment.access_suspended = True
        enrollment.payment_status = PaymentStatus.FAILED
    else:
        # Fewer than 2 misses currently — access depends on current payment state.
        # Auto-revert DROPOUT_PENDING: the trigger that caused it (2 misses) is
        # no longer true, so lift the pending dropout. DROPPED is left alone —
        # an admin must explicitly reverse a confirmed drop.
        if enrollment.status == EnrollmentStatus.DROPOUT_PENDING:
            enrollment.status = EnrollmentStatus.ENROLLED
            enrollment.dropped_at = None

        enrollment.access_suspended = is_required_installment_unpaid
        if paid_count == 0:
            enrollment.payment_status = PaymentStatus.PENDING
        elif is_required_installment_unpaid:
            enrollment.payment_status = PaymentStatus.FAILED
        else:
            enrollment.payment_status = PaymentStatus.PAID
            if (
                enrollment.status == EnrollmentStatus.PENDING_APPROVAL
                and not cohort_requires_approval
            ):
                enrollment.status = EnrollmentStatus.ENROLLED

    if total > 0 and paid_count >= total:
        enrollment.paid_at = effective_now
