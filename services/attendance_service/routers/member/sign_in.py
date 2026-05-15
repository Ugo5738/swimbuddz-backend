"""Member-facing sign-in endpoints (authenticated + public)."""

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from libs.common.currency import kobo_to_bubbles
from libs.common.service_client import debit_member_wallet, get_session_by_id
from libs.db.session import get_async_db
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.attendance_service.models import (
    AttendanceRecord,
    AttendanceStatus,
    MemberRef,
)
from services.attendance_service.schemas import (
    AttendanceCreate,
    AttendanceResponse,
    PublicAttendanceCreate,
)

from ._milestones import _check_attendance_milestones
from ._shared import get_current_member, validate_session_access

router = APIRouter()


@router.post("/sessions/{session_id}/sign-in", response_model=AttendanceResponse)
async def sign_in_to_session(
    session_id: uuid.UUID,
    attendance_in: AttendanceCreate,
    current_member: MemberRef = Depends(get_current_member),
    db: AsyncSession = Depends(get_async_db),
):
    """
    Sign in to a session. Idempotent upsert.
    When pay_with_bubbles=True the member's wallet is debited for the session fee
    (only on the first sign-in, not on subsequent upserts).
    """
    # Verify session exists (via sessions-service)
    session_data = await get_session_by_id(
        str(session_id), calling_service="attendance"
    )
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")

    # Enforce tier-based access control (admins/coaches skip this check
    # since they need to mark attendance for any session)
    await validate_session_access(session_data, str(current_member.id))

    # Check for existing attendance
    query = select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.member_id == current_member.id,
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()
    is_new = attendance is None

    wallet_txn_id = None

    # Debit wallet on first sign-in when requested and session has a fee
    if (
        is_new
        and attendance_in.pay_with_bubbles
        and attendance_in.status in (AttendanceStatus.PRESENT, AttendanceStatus.LATE)
    ):
        pool_fee_kobo = session_data.get("pool_fee") or 0
        if pool_fee_kobo > 0:
            fee_bubbles = kobo_to_bubbles(pool_fee_kobo)
            idempotency_key = f"session-fee-{session_id}-{current_member.id}"
            try:
                result_txn = await debit_member_wallet(
                    current_member.auth_id,
                    amount=fee_bubbles,
                    idempotency_key=idempotency_key,
                    description=f"Session fee — {session_data.get('title', '')} ({fee_bubbles} 🫧)",
                    calling_service="attendance",
                    transaction_type="purchase",
                    reference_type="session",
                    reference_id=str(session_id),
                )
                wallet_txn_id = result_txn.get("transaction_id")
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 400:
                    detail = e.response.json().get("detail", "")
                    if "Insufficient" in detail:
                        raise HTTPException(
                            status_code=402,
                            detail="Insufficient Bubbles. Please top up your wallet.",
                        )
                    if "frozen" in detail.lower() or "suspended" in detail.lower():
                        raise HTTPException(
                            status_code=403,
                            detail="Wallet is inactive. Please contact support.",
                        )
                raise

    if attendance:
        # Update existing
        attendance.status = attendance_in.status
        attendance.role = attendance_in.role
        attendance.notes = attendance_in.notes
    else:
        # Create new
        attendance = AttendanceRecord(
            session_id=session_id,
            member_id=current_member.id,
            status=attendance_in.status,
            role=attendance_in.role,
            notes=attendance_in.notes,
            wallet_transaction_id=wallet_txn_id,
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)

    # Best-effort: check attendance milestones after new sign-in
    if is_new and attendance_in.status in (
        AttendanceStatus.PRESENT,
        AttendanceStatus.LATE,
    ):
        await _check_attendance_milestones(
            db, current_member.id, current_member.auth_id
        )

    return attendance


@router.post(
    "/sessions/{session_id}/attendance/public", response_model=AttendanceResponse
)
async def public_sign_in_to_session(
    session_id: uuid.UUID,
    attendance_in: PublicAttendanceCreate,
    db: AsyncSession = Depends(get_async_db),
):
    """
    Public sign in to a session (no auth required). Idempotent upsert.
    """
    # Verify session exists (via sessions-service)
    session_data = await get_session_by_id(
        str(session_id), calling_service="attendance"
    )
    if not session_data:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify member exists
    query = select(MemberRef).where(MemberRef.id == attendance_in.member_id)
    result = await db.execute(query)
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=404, detail="Member not found")

    # Enforce tier-based access control
    await validate_session_access(session_data, str(attendance_in.member_id))

    # Check for existing attendance
    query = select(AttendanceRecord).where(
        AttendanceRecord.session_id == session_id,
        AttendanceRecord.member_id == attendance_in.member_id,
    )
    result = await db.execute(query)
    attendance = result.scalar_one_or_none()

    if attendance:
        # Update existing
        attendance.status = attendance_in.status
        attendance.role = attendance_in.role
        attendance.notes = attendance_in.notes
    else:
        # Create new
        attendance = AttendanceRecord(
            session_id=session_id,
            member_id=attendance_in.member_id,
            status=attendance_in.status,
            role=attendance_in.role,
            notes=attendance_in.notes,
        )
        db.add(attendance)

    await db.commit()
    await db.refresh(attendance)
    return attendance
