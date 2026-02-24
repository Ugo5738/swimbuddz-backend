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
THREE_INSTALLMENT_CAP_THRESHOLD = 150_000
MAX_INSTALLMENTS_OVER_CAP = 3
WAT_TZ = ZoneInfo("Africa/Lagos")

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
    if total_fee > THREE_INSTALLMENT_CAP_THRESHOLD:
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
    count_override: int | None = None,
    deposit_override: int | None = None,
) -> list[dict]:
    """
    Build the installment schedule for an enrollment.

    - ``count_override``: admin-set total number of installments; defaults to
      ``installment_count(total_fee, duration_weeks)``.
    - ``deposit_override``: admin-set first-installment amount (₦); if set,
      the remainder is split evenly across the remaining installments. Defaults
      to auto even-split via ``split_amounts``.
    """
    count = (
        count_override
        if count_override and count_override >= 2
        else installment_count(total_fee, duration_weeks)
    )

    if deposit_override is not None and deposit_override > 0 and count >= 2:
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

    anchor_wat = monday_00_wat(cohort_start)

    schedule: list[dict] = []
    for idx, amount in enumerate(amounts, start=1):
        due_wat = anchor_wat + timedelta(weeks=(idx - 1) * FOUR_WEEK_BLOCK_WEEKS)
        schedule.append(
            {
                "installment_number": idx,
                "amount": amount,
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

    Key behavioral rules:
    - missed_installments_count is a permanent behavioral counter. It only ever goes up.
      Paying late restores access but does NOT reduce the missed count.
    - At missed_count >= 2:
        - If admin_dropout_approval is True on the cohort: move to DROPOUT_PENDING (admin must confirm).
        - Otherwise: move directly to DROPPED.
    - Suspension triggers when a required installment is unpaid (after grace window).
    - Paying a late installment lifts suspension immediately.
    - access_suspended reflects current payment state, not behavioral history.
    """
    effective_now = now or utc_now()
    total = len(installments)
    paid_statuses = {InstallmentStatus.PAID, InstallmentStatus.WAIVED}
    paid_count = sum(1 for i in installments if i.status in paid_statuses)

    # IMPORTANT: missed_count is counted from the installments table only.
    # We do NOT use enrollment.missed_installments_count as source of truth here
    # because it's a derived/cached value. The installments list is authoritative.
    # Once an installment is MISSED it stays MISSED even if paid later — the
    # installment record itself tracks the behavioral history.
    missed_count = sum(1 for i in installments if i.status == InstallmentStatus.MISSED)

    enrollment.total_installments = total
    enrollment.paid_installments_count = paid_count
    # Never decrease missed_installments_count — it is a permanent behavioral counter.
    # If the DB value is already higher than what we count (shouldn't happen but defensive),
    # keep the higher value.
    enrollment.missed_installments_count = max(
        missed_count, enrollment.missed_installments_count
    )

    if enrollment.status == EnrollmentStatus.WAITLIST:
        enrollment.access_suspended = False
        enrollment.payment_status = (
            PaymentStatus.PAID if paid_count > 0 else PaymentStatus.PENDING
        )
        return

    required_block = current_block_number(
        now=effective_now,
        cohort_start=cohort_start,
        duration_weeks=duration_weeks,
    )
    required_installments = min(required_block, total)

    is_required_installment_unpaid = any(
        i.installment_number <= required_installments and i.status not in paid_statuses
        for i in installments
    )

    # Use the definitive behavioral count from the model (never decreases)
    effective_missed = enrollment.missed_installments_count

    if effective_missed >= 2:
        # Do not re-trigger if already fully dropped (e.g. admin already confirmed)
        if enrollment.status not in (
            EnrollmentStatus.DROPPED,
            EnrollmentStatus.DROPOUT_PENDING,
        ):
            if admin_dropout_approval:
                # Requires admin confirmation before dropping
                enrollment.status = EnrollmentStatus.DROPOUT_PENDING
            else:
                enrollment.status = EnrollmentStatus.DROPPED

        enrollment.access_suspended = True
        enrollment.payment_status = PaymentStatus.FAILED
    else:
        # Fewer than 2 misses — access depends solely on current payment state
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
