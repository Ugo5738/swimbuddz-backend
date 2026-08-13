"""Authoritative Academy installment and annual-membership quote."""

import uuid
from datetime import datetime

import httpx
from fastapi import HTTPException, status

from libs.auth.dependencies import _service_role_jwt
from libs.common.config import get_settings
from libs.common.currency import KOBO_PER_NAIRA
from libs.common.service_client import get_member_by_auth_id


settings = get_settings()
PAID_INSTALLMENT_STATUSES = {"paid", "waived"}


async def academy_payment_context(
    *,
    enrollment_id: uuid.UUID,
    member_auth_id: str,
    use_installments: bool,
    amount_override_kobo: int | None = None,
) -> dict:
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{settings.ACADEMY_SERVICE_URL}/internal/academy/enrollments/{enrollment_id}",
            params={"use_installments": str(use_installments).lower()},
            headers=headers,
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to fetch enrollment: {response.text}",
        )
    enrollment = response.json()
    member = await get_member_by_auth_id(
        member_auth_id,
        calling_service="payments",
    )
    if not member or str(member.get("id")) != str(enrollment.get("member_id")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This Academy enrollment belongs to another member",
        )

    program = enrollment.get("program") or {}
    cohort = enrollment.get("cohort") or {}
    installments = sorted(
        enrollment.get("installments") or [],
        key=lambda item: item.get("installment_number", 0),
    )
    next_installment = next(
        (
            item
            for item in installments
            if str(item.get("status") or "").lower() not in PAID_INSTALLMENT_STATUSES
        ),
        None,
    )
    if next_installment:
        academy_amount_kobo = int(next_installment.get("amount") or 0)
    else:
        academy_amount_kobo = int(
            round(
                float(
                    cohort.get("price_override")
                    if cohort.get("price_override") is not None
                    else (program.get("price_amount") or 0)
                )
                * KOBO_PER_NAIRA
            )
        )
        if str(enrollment.get("payment_status") or "").lower() == "paid":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="All required Academy installments are already paid",
            )

    if amount_override_kobo is not None and amount_override_kobo > 0:
        if amount_override_kobo < academy_amount_kobo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Custom amount is less than the next stipulated installment",
            )
        remaining_balance_kobo = sum(
            int(item.get("amount") or 0)
            for item in installments
            if str(item.get("status") or "").lower() not in PAID_INSTALLMENT_STATUSES
        )
        if remaining_balance_kobo and amount_override_kobo > remaining_balance_kobo:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Custom amount exceeds the remaining Academy balance",
            )
        academy_amount_kobo = amount_override_kobo

    if academy_amount_kobo <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No payable installment is available for this enrollment",
        )

    paid_installments = [
        item
        for item in installments
        if str(item.get("status") or "").lower() in PAID_INSTALLMENT_STATUSES
    ]
    first_payment = (
        not paid_installments
        and str(enrollment.get("payment_status") or "").lower() != "paid"
    )
    membership_policy = (
        cohort.get("membership_policy_override")
        or program.get("membership_policy")
        or "open"
    )
    membership_months = 0
    membership_fee_kobo = 0
    if first_payment and membership_policy in {"active_required", "included"}:
        membership_covers_cohort = False
        if membership_policy == "active_required":
            membership = member.get("membership") or {}
            paid_until_value = membership.get("community_paid_until")
            cohort_end_value = cohort.get("end_date")
            if paid_until_value and cohort_end_value:
                paid_until = datetime.fromisoformat(
                    paid_until_value.replace("Z", "+00:00")
                )
                cohort_end = datetime.fromisoformat(
                    cohort_end_value.replace("Z", "+00:00")
                )
                membership_covers_cohort = paid_until >= cohort_end
        if membership_policy == "included" or not membership_covers_cohort:
            membership_months = 12
        if membership_policy == "active_required" and not membership_covers_cohort:
            membership_fee_kobo = int(
                getattr(settings, "COMMUNITY_ANNUAL_FEE_NGN", 20_000) * KOBO_PER_NAIRA
            )

    return {
        "currency": "NGN",
        "subtotal_kobo": academy_amount_kobo + membership_fee_kobo,
        "academy_amount_kobo": academy_amount_kobo,
        "annual_membership_fee_kobo": membership_fee_kobo,
        "annual_membership_months": membership_months,
        "membership_policy": membership_policy,
        "enrollment_id": str(enrollment_id),
        "cohort_id": str(enrollment.get("cohort_id") or "") or None,
        "installment_id": (
            str(next_installment.get("id")) if next_installment else None
        ),
        "installment_number": (
            int(next_installment.get("installment_number"))
            if next_installment and next_installment.get("installment_number")
            else None
        ),
        "installment_due_at": (
            next_installment.get("due_at") if next_installment else None
        ),
        "total_installments": (int(enrollment.get("total_installments") or 0) or None),
    }
