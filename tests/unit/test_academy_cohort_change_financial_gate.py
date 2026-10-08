"""Fail-closed financial gate for Academy cohort switches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.academy_service.routers.enrollments import change_cohort


@pytest.mark.asyncio
async def test_change_gate_blocks_when_payments_unavailable(monkeypatch):
    monkeypatch.setattr(
        change_cohort,
        "get_settings",
        lambda: SimpleNamespace(PAYMENTS_SERVICE_URL="http://payments"),
    )
    monkeypatch.setattr(
        change_cohort,
        "internal_get",
        AsyncMock(side_effect=RuntimeError("payments service unavailable")),
    )

    with pytest.raises(HTTPException) as exc:
        await change_cohort.financial_state(uuid4())

    assert exc.value.status_code == 503
    assert "No enrollment was changed" in exc.value.detail


@pytest.mark.asyncio
async def test_change_gate_preserves_pending_review_reference(monkeypatch):
    reference = "PAY-12345"
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {
            "has_payment_activity": True,
            "references": [reference],
            "statuses": ["pending_review"],
        },
    )
    monkeypatch.setattr(
        change_cohort,
        "get_settings",
        lambda: SimpleNamespace(PAYMENTS_SERVICE_URL="http://payments"),
    )
    fetch = AsyncMock(return_value=response)
    monkeypatch.setattr(change_cohort, "internal_get", fetch)

    result = await change_cohort.financial_state(uuid4())

    assert result["has_payment_activity"] is True
    assert result["references"] == [reference]
    assert result["statuses"] == ["pending_review"]
    fetch.assert_awaited_once()
