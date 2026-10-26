"""Service-authenticated receipt upload for accountless payment capabilities."""

import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from libs.auth.dependencies import require_service_role
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.media_service.routers.media import upload_file
from services.media_service.models import MediaItem
from services.media_service.services.payment_receipt_extract import extract_receipt_hints

router = APIRouter(prefix="/media/internal", tags=["internal"])


@router.get("/payment-proof/{media_id}", dependencies=[Depends(require_service_role)])
async def payment_proof_metadata(
    media_id: uuid.UUID, db: AsyncSession = Depends(get_async_db)
):
    """Validate receipt ownership without issuing a private file URL."""
    media = await db.get(MediaItem, media_id)
    if not media or (media.metadata_info or {}).get("purpose") != "payment_proof":
        raise HTTPException(404, "Receipt not found")
    return {
        "id": str(media.id),
        "uploaded_by": str(media.uploaded_by),
        "metadata": {
            "purpose": "payment_proof",
            "linked_id": (media.metadata_info or {}).get("linked_id"),
            "receipt_extraction": (media.metadata_info or {}).get("receipt_extraction"),
        },
    }


@router.post("/payment-proof", dependencies=[Depends(require_service_role)])
async def upload_payment_proof(
    payment_id: uuid.UUID = Form(...),
    reference: str = Form(..., min_length=1, max_length=128),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_async_db),
):
    # OCR is best-effort only; no raw receipt text is persisted.
    data = await file.read(10 * 1024 * 1024 + 1)
    await file.seek(0)
    hints = (
        await run_in_threadpool(extract_receipt_hints, data, file.content_type or "")
        if len(data) <= 10 * 1024 * 1024
        else {"status": "unavailable"}
    )
    # A receipt-only system principal, not a guest pretending to be a member.
    owner = uuid.uuid5(uuid.NAMESPACE_URL, f"swimbuddz:payment-proof:{payment_id}")
    media = await upload_file(
        file=file,
        purpose="payment_proof",
        linked_id=reference,
        title=f"Payment proof {reference}",
        description="Submitted through private payment capability",
        current_user=AuthUser(sub=str(owner), role="service_role"),
        db=db,
    )
    row = await db.get(MediaItem, media.id)
    if row is not None:
        row.metadata_info = {
            **(row.metadata_info or {}),
            "receipt_extraction": hints,
        }
        await db.commit()
    return {"id": str(media.id)}
