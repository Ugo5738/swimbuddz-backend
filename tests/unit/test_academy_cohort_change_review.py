"""Focused tests for Academy transfer review ownership and state transitions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.academy_service.routers.enrollments.change_cohort import (
    reject_enrollment_change,
)


@pytest.mark.asyncio
async def test_admin_rejection_is_idempotent_and_preserves_enrollment():
    change = SimpleNamespace(
        id=uuid4(),
        state="needs_review",
        snapshot={"payment_references": ["PAY-123"]},
    )
    scalar = SimpleNamespace(scalar_one_or_none=lambda: change)
    db = SimpleNamespace(execute=AsyncMock(return_value=scalar), commit=AsyncMock())
    admin = SimpleNamespace(user_id="admin-1")
    result = await reject_enrollment_change(change.id, admin, db)
    assert result["state"] == "rejected"
    assert change.snapshot["payment_references"] == ["PAY-123"]
    assert change.snapshot["reviewed_by_auth_id"] == "admin-1"
    db.commit.assert_awaited_once()
    await reject_enrollment_change(change.id, admin, db)
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_transfer_cannot_be_rejected():
    change = SimpleNamespace(id=uuid4(), state="completed", snapshot={})
    scalar = SimpleNamespace(scalar_one_or_none=lambda: change)
    db = SimpleNamespace(execute=AsyncMock(return_value=scalar), commit=AsyncMock())
    with pytest.raises(HTTPException) as exc:
        await reject_enrollment_change(change.id, SimpleNamespace(user_id="admin"), db)
    assert exc.value.status_code == 409
    db.commit.assert_not_awaited()
