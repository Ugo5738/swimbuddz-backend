from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from services.payments_service.models import Payment

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("/generate-reference")
async def generate_payment_reference(
    current_user: AuthUser = Depends(require_admin),
):
    """
    Generate a unique payment reference.
    """
    # In a real app, we might reserve this in DB immediately
    ref = Payment.generate_reference()
    return {"reference": ref}


@router.get("/")
async def list_payments(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """
    List payments (Stub).
    """
    return {"message": "Not implemented yet"}
