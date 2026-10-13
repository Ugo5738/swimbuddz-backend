"""An accidental enrollment can be deleted only after authoritative safeguards pass."""
import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.academy_service.models import Enrollment, EnrollmentStatus, PaymentStatus
from services.academy_service.routers.enrollments import delete_empty

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_empty_enrollment_preview_and_delete(db_session, monkeypatch):
    record = Enrollment(member_id=uuid.uuid4(), member_auth_id="test-auth",
                        status=EnrollmentStatus.PENDING_APPROVAL,
                        payment_status=PaymentStatus.PENDING)
    db_session.add(record)
    await db_session.commit()
    monkeypatch.setattr(delete_empty, "financial_state", AsyncMock(return_value={
        "has_payment_activity": False, "references": [], "attempts": [],
        "verified_paid_tuition_kobo": 0, "blocked_references": [],
    }))
    admin = type("Admin", (), {"user_id": "test-admin"})()
    preview = await delete_empty.deletion_preview(record.id, admin, db_session)
    assert preview["eligible"] is True
    payload = delete_empty.ConfirmDelete(
        preview_token=preview["preview_token"], confirmation=str(record.id),
        reason="Duplicate enrollment created during admin testing",
    )
    answer = await delete_empty.delete_empty_enrollment(record.id, payload, admin, db_session)
    assert answer["deleted"] is True
    assert await db_session.get(Enrollment, record.id) is None


async def test_payment_attempts_block_delete_even_if_pending(db_session, monkeypatch):
    record = Enrollment(member_id=uuid.uuid4(), member_auth_id="test-auth",
                        status=EnrollmentStatus.PENDING_APPROVAL,
                        payment_status=PaymentStatus.PENDING)
    db_session.add(record)
    await db_session.commit()
    monkeypatch.setattr(delete_empty, "financial_state", AsyncMock(return_value={
        "has_payment_activity": True, "references": ["PAY-test"],
        "attempts": [{"reference": "PAY-test", "status": "pending"}],
        "verified_paid_tuition_kobo": 0, "blocked_references": ["PAY-test"],
    }))
    admin = type("Admin", (), {"user_id": "test-admin"})()
    preview = await delete_empty.deletion_preview(record.id, admin, db_session)
    assert not preview["eligible"]
    assert "payment_attempts_exist" in preview["blockers"]
    payload = delete_empty.ConfirmDelete(
        preview_token=preview["preview_token"], confirmation=str(record.id),
        reason="Accidental enrollment created during admin testing",
    )
    with pytest.raises(HTTPException) as error:
        await delete_empty.delete_empty_enrollment(record.id, payload, admin, db_session)
    assert error.value.status_code == 409
    assert await db_session.get(Enrollment, record.id) is not None
