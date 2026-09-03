"""Additional payment-charge policies and member-facing previews."""

import uuid
from typing import Literal, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import _service_role_jwt, get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.db.session import get_async_db
from services.payments_service.models import AdditionalChargePolicy, PaymentPurpose
from services.payments_service.services.additional_charges import (
    calculate_additional_charges,
)
from services.payments_service.services.academy_pricing import (
    academy_payment_context,
)

router = APIRouter(prefix="/payments/charges", tags=["payment-charges"])
settings = get_settings()


class ChargePolicyCreate(BaseModel):
    purpose: str = Field(..., min_length=1, max_length=40)
    payment_method: Optional[str] = Field(default=None, max_length=32)
    label: str = Field(..., min_length=1, max_length=120)
    calculation_mode: str = Field(default="additive", pattern="^(additive|gross_up)$")
    rate_basis_points: int = Field(default=0, ge=0, le=9999)
    fixed_amount_kobo: int = Field(default=0, ge=0)
    cap_amount_kobo: Optional[int] = Field(default=None, ge=0)
    waive_fixed_below_kobo: Optional[int] = Field(default=None, ge=0)
    is_active: bool = True


class ChargePolicyUpdate(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=120)
    calculation_mode: Optional[str] = Field(
        default=None, pattern="^(additive|gross_up)$"
    )
    rate_basis_points: Optional[int] = Field(default=None, ge=0, le=9999)
    fixed_amount_kobo: Optional[int] = Field(default=None, ge=0)
    cap_amount_kobo: Optional[int] = Field(default=None, ge=0)
    waive_fixed_below_kobo: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


class ChargePolicyResponse(ChargePolicyCreate):
    id: uuid.UUID
    created_by_auth_id: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class ChargePreviewRequest(BaseModel):
    purpose: PaymentPurpose
    payment_method: str = "paystack"
    club_application_id: Optional[uuid.UUID] = None
    club_payment_mode: Optional[
        Literal["quarterly_prepaid", "transition_per_session"]
    ] = None
    community_experience_offering_id: Optional[uuid.UUID] = None
    enrollment_id: Optional[uuid.UUID] = None
    use_installments: bool = False
    amount_override_kobo: Optional[int] = Field(default=None, ge=0)
    subtotal_kobo: Optional[int] = Field(default=None, ge=0)


class ChargePreviewResponse(BaseModel):
    currency: str = "NGN"
    subtotal_kobo: int
    additional_charges: list[dict]
    additional_charges_total_kobo: int
    total_kobo: int
    components: dict = Field(default_factory=dict)


async def _club_context(application_id: uuid.UUID, payment_mode: str | None) -> dict:
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{settings.MEMBERS_SERVICE_URL}/clubs/internal/applications/{application_id}/payment-context",
            params={"payment_mode": payment_mode} if payment_mode else None,
            headers=headers,
        )
    if response.status_code >= 400:
        detail = "Could not price this Club application"
        try:
            detail = response.json().get("detail") or detail
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=detail)
    return response.json()


async def _community_experience_context(
    offering_id: uuid.UUID, member_auth_id: str
) -> dict:
    headers = {"Authorization": f"Bearer {_service_role_jwt('payments')}"}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            (
                f"{settings.MEMBERS_SERVICE_URL}/clubs/community-experiences/"
                f"internal/{offering_id}/payment-context"
            ),
            params={"member_auth_id": member_auth_id},
            headers=headers,
        )
    if response.status_code >= 400:
        detail = "Could not price this Community Experience"
        try:
            detail = response.json().get("detail") or detail
        except ValueError:
            pass
        raise HTTPException(status_code=response.status_code, detail=detail)
    return response.json()


@router.get("", response_model=list[ChargePolicyResponse])
async def list_charge_policies(
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    return list(
        (
            await db.execute(
                select(AdditionalChargePolicy).order_by(
                    AdditionalChargePolicy.purpose, AdditionalChargePolicy.label
                )
            )
        ).scalars()
    )


@router.post(
    "", response_model=ChargePolicyResponse, status_code=status.HTTP_201_CREATED
)
async def create_charge_policy(
    body: ChargePolicyCreate,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    if body.purpose != "*" and body.purpose not in {
        item.value for item in PaymentPurpose
    }:
        raise HTTPException(status_code=400, detail="Unknown payment purpose")
    policy = AdditionalChargePolicy(
        **body.model_dump(), created_by_auth_id=admin.user_id
    )
    db.add(policy)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="A policy with this scope and label already exists"
        ) from exc
    await db.refresh(policy)
    return policy


@router.patch("/{policy_id}", response_model=ChargePolicyResponse)
async def update_charge_policy(
    policy_id: uuid.UUID,
    body: ChargePolicyUpdate,
    _admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    policy = await db.get(AdditionalChargePolicy, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Charge policy not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(policy, field, value)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.post("/preview", response_model=ChargePreviewResponse)
async def preview_additional_charges(
    body: ChargePreviewRequest,
    current_user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    components: dict = {}
    currency = "NGN"
    if body.purpose == PaymentPurpose.CLUB and body.club_application_id:
        context = await _club_context(body.club_application_id, body.club_payment_mode)
        if context["member_auth_id"] != current_user.user_id:
            raise HTTPException(
                status_code=403,
                detail="This Club application belongs to another member",
            )
        subtotal_kobo = int(context["subtotal_kobo"])
        currency = context["currency"]
        components = {
            "club": int(context["club_fee_kobo"]),
            "club_items": context.get("club_items") or [],
            "club_payment_mode": context["payment_mode"],
            "approved_payment_modes": context.get("approved_payment_modes") or [],
            "transition_session_rate_kobo": context.get("transition_session_rate_kobo"),
            "transition_expires_at": context.get("transition_expires_at"),
            "annual_swimbuddz_membership": int(
                context.get("annual_membership_fee_kobo") or 0
            ),
            "community_experience": int(context["community_experience_fee_kobo"]),
            "community_experience_selected": context["community_experience_selected"],
        }
    elif (
        body.purpose == PaymentPurpose.COMMUNITY_EXPERIENCE
        and body.community_experience_offering_id
    ):
        context = await _community_experience_context(
            body.community_experience_offering_id,
            current_user.user_id,
        )
        subtotal_kobo = int(context["subtotal_kobo"])
        currency = context["currency"]
        components = {
            "community_experience": int(context["amount_kobo"]),
            "annual_swimbuddz_membership": int(
                context.get("annual_membership_fee_kobo") or 0
            ),
            "price_context": context["price_context"],
        }
    elif body.purpose == PaymentPurpose.ACADEMY_COHORT and body.enrollment_id:
        context = await academy_payment_context(
            enrollment_id=body.enrollment_id,
            member_auth_id=current_user.user_id,
            use_installments=body.use_installments,
            amount_override_kobo=body.amount_override_kobo,
        )
        subtotal_kobo = int(context["subtotal_kobo"])
        currency = context["currency"]
        components = {
            "academy": int(context["academy_amount_kobo"]),
            "annual_swimbuddz_membership": int(context["annual_membership_fee_kobo"]),
            "academy_membership_policy": context["membership_policy"],
            "annual_membership_months": int(context["annual_membership_months"]),
            "installment_number": context["installment_number"],
            "total_installments": context["total_installments"],
        }
    elif body.subtotal_kobo is not None:
        subtotal_kobo = body.subtotal_kobo
    else:
        raise HTTPException(
            status_code=400, detail="An authoritative pricing context is required"
        )
    lines, charge_total = await calculate_additional_charges(
        db,
        purpose=body.purpose,
        payment_method=body.payment_method,
        subtotal_kobo=subtotal_kobo,
    )
    return ChargePreviewResponse(
        currency=currency,
        subtotal_kobo=subtotal_kobo,
        additional_charges=lines,
        additional_charges_total_kobo=charge_total,
        total_kobo=subtotal_kobo + charge_total,
        components=components,
    )
