"""Authoritative additional-charge calculation for payment intents."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.payments_service.models import AdditionalChargePolicy, PaymentPurpose


async def calculate_additional_charges(
    db: AsyncSession,
    *,
    purpose: PaymentPurpose,
    payment_method: str,
    subtotal_kobo: int,
) -> tuple[list[dict], int]:
    rows = list(
        (
            await db.execute(
                select(AdditionalChargePolicy)
                .where(
                    AdditionalChargePolicy.is_active.is_(True),
                    AdditionalChargePolicy.purpose.in_([purpose.value, "*"]),
                    or_(
                        AdditionalChargePolicy.payment_method.is_(None),
                        AdditionalChargePolicy.payment_method == payment_method,
                    ),
                )
                .order_by(AdditionalChargePolicy.created_at, AdditionalChargePolicy.id)
            )
        ).scalars()
    )
    lines: list[dict] = []
    total = 0
    for policy in rows:
        fixed = policy.fixed_amount_kobo
        if (
            policy.waive_fixed_below_kobo is not None
            and subtotal_kobo < policy.waive_fixed_below_kobo
        ):
            fixed = 0
        amount = ((subtotal_kobo * policy.rate_basis_points + 5_000) // 10_000) + fixed
        if policy.cap_amount_kobo is not None:
            amount = min(amount, policy.cap_amount_kobo)
        if amount <= 0:
            continue
        lines.append(
            {
                "policy_id": str(policy.id),
                "label": policy.label,
                "amount_kobo": amount,
                "rate_basis_points": policy.rate_basis_points,
                "fixed_amount_kobo": fixed,
            }
        )
        total += amount
    return lines, total
