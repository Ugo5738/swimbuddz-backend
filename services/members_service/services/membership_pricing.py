"""Pricing helpers for the annual SwimBuddz Membership.

Programme checkouts may cover dates beyond the member's current Membership
expiry. Membership is sold in annual blocks, so the checkout must include
enough whole 12-month blocks to cover the last selected programme date.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from dateutil.relativedelta import relativedelta


def annual_membership_extension(
    *,
    paid_until: datetime | None,
    coverage_end: date,
    annual_fee_kobo: int,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Return ``(months, fee_kobo)`` needed to cover ``coverage_end``.

    Existing unexpired time is preserved because the entitlement endpoint
    extends from the current expiry. If Membership is absent or expired, the
    first annual block starts now. No charge is returned when the existing
    entitlement already covers the requested date.
    """

    current = now or datetime.now(timezone.utc)
    current_date = current.date()
    paid_until_date = paid_until.date() if paid_until else None
    if paid_until_date and paid_until_date >= coverage_end:
        return 0, 0

    base = (
        paid_until
        if paid_until and paid_until_date and paid_until_date > current_date
        else current
    )
    months = 0
    # The caller only reaches this loop when another annual block is needed.
    while months == 0 or (base + relativedelta(months=months)).date() < coverage_end:
        months += 12
    return months, (months // 12) * annual_fee_kobo
