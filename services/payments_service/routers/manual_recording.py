"""Admin reconciliation for every payment purpose and private guest transfers."""

from datetime import datetime
from zoneinfo import ZoneInfo
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import _service_role_jwt, require_admin
from libs.common.config import get_settings
from libs.auth.models import AuthUser
from libs.common.currency import naira_to_kobo
from libs.db.session import get_async_db
from services.payments_service.models import Payment, PaymentStatus
from services.payments_service.schemas import PaymentResponse
from services.payments_service.schemas.manual_recording import (
    AttachPaymentReceipt,
    OfflinePaymentRecord,
    TransferAccess,
    TransferReceipt,
    TransferSummary,
)
from services.payments_service.services.manual_transfer import (
    check_transfer_token,
    settle_offline,
    validate_receipt_media,
)

router = APIRouter(prefix="/payments", tags=["manual-payments"])


async def find_payment(db, reference: str, *, lock=False):
    query = select(Payment).where(Payment.reference == reference)
    if lock:
        query = query.with_for_update()
    payment = (await db.execute(query)).scalar_one_or_none()
    if payment is None:
        raise HTTPException(404, "Payment not found")
    return payment


def summary(payment):
    metadata = payment.payment_metadata or {}
    return TransferSummary(
        reference=payment.reference,
        purpose=payment.purpose.value,
        amount_kobo=naira_to_kobo(payment.amount),
        currency=payment.currency,
        status=payment.status.value,
        fulfilled=bool(payment.entitlement_applied_at),
        receipt_attached=bool(payment.proof_of_payment_media_id),
        reservation_expires_at=metadata.get("reservation_expires_at")
        or (metadata.get("club_capacity_reservation") or {}).get("expires_at"),
    )


@router.post("/manual-transfer/{reference}/view", response_model=TransferSummary)
async def view_transfer(
    reference: str, body: TransferAccess, db: AsyncSession = Depends(get_async_db)
):
    check_transfer_token(reference, body.access_token)
    payment = await find_payment(db, reference)
    if payment.payment_method not in {"manual_transfer", "bank_transfer"}:
        raise HTTPException(409, "This is not a bank-transfer checkout")
    return summary(payment)


@router.post("/manual-transfer/{reference}/upload", response_model=TransferSummary)
async def upload_transfer_receipt(
    reference: str,
    access_token: str = Form(..., min_length=64, max_length=64),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
):
    check_transfer_token(reference, access_token)
    payment = await find_payment(db, reference, lock=True)
    if payment.payment_method != "manual_transfer" or payment.status not in {
        PaymentStatus.PENDING,
        PaymentStatus.FAILED,
    }:
        raise HTTPException(
            409, "Receipt can only be uploaded before submitting for review"
        )
    if payment.proof_of_payment_media_id:
        raise HTTPException(
            409, "A receipt is already attached; submit its transfer details"
        )
    data = await file.read(10 * 1024 * 1024 + 1)
    if len(data) > 10 * 1024 * 1024:
        raise HTTPException(413, "Receipt must be 10 MB or smaller")
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{get_settings().MEDIA_SERVICE_URL}/media/internal/payment-proof",
            headers={"Authorization": f"Bearer {_service_role_jwt('payments')}"},
            data={"payment_id": str(payment.id), "reference": payment.reference},
            files={
                "file": (
                    file.filename or "receipt",
                    data,
                    file.content_type or "application/octet-stream",
                )
            },
        )
    if response.status_code >= 400:
        raise HTTPException(
            422, "Receipt upload failed; use a PDF or image up to 10 MB"
        )
    payment.proof_of_payment_media_id = UUID(response.json()["id"])
    await db.commit()
    return summary(payment)


@router.post("/manual-transfer/{reference}/submit", response_model=TransferSummary)
async def submit_transfer(
    reference: str, body: TransferReceipt, db: AsyncSession = Depends(get_async_db)
):
    check_transfer_token(reference, body.access_token)
    payment = await find_payment(db, reference, lock=True)
    if payment.payment_method != "manual_transfer":
        raise HTTPException(409, "This is not a pending bank-transfer checkout")
    receipt = body.model_dump(exclude={"access_token"}, mode="json")
    if (
        payment.status == PaymentStatus.PENDING_REVIEW
        and (payment.payment_metadata or {}).get("submitted_transfer") == receipt
    ):
        return summary(payment)
    if payment.status not in {PaymentStatus.PENDING, PaymentStatus.FAILED}:
        raise HTTPException(409, "This payment has already been submitted or settled")
    if body.received_date > datetime.now(ZoneInfo("Africa/Lagos")).date():
        raise HTTPException(422, "Transfer date cannot be in the future")
    payment.payment_metadata = {
        **(payment.payment_metadata or {}),
        "submitted_transfer": receipt,
    }
    payment.status = PaymentStatus.PENDING_REVIEW
    payment.admin_review_note = None
    await db.commit()
    return summary(payment)


@router.get("/admin/recording", response_model=list[PaymentResponse])
async def search_recordable_payments(
    search: str = Query(min_length=3, max_length=160),
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    pattern = (
        "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    )
    result = await db.execute(
        select(Payment)
        .where(
            or_(
                Payment.reference.ilike(pattern, escape="\\"),
                Payment.payer_email.ilike(pattern, escape="\\"),
            )
        )
        .order_by(Payment.created_at.desc())
        .limit(30)
    )
    return result.scalars().all()


@router.post("/admin/{reference}/offline-payment", response_model=PaymentResponse)
async def record_offline_payment(
    reference: str,
    body: OfflinePaymentRecord,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    payment = await find_payment(db, reference)
    return await settle_offline(db, payment, body, admin)


@router.post("/admin/{reference}/receipt", response_model=PaymentResponse)
async def attach_receipt(
    reference: str,
    body: AttachPaymentReceipt,
    admin: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    payment = await find_payment(db, reference, lock=True)
    if (
        payment.proof_of_payment_media_id
        and payment.proof_of_payment_media_id != body.proof_media_id
    ):
        raise HTTPException(
            409, "A receipt is already attached; do not replace audit evidence"
        )
    await validate_receipt_media(body.proof_media_id, payment, admin)
    payment.proof_of_payment_media_id = body.proof_media_id
    payment.payment_metadata = {
        **(payment.payment_metadata or {}),
        "receipt_attached_by": admin.user_id,
    }
    await db.commit()
    return payment
