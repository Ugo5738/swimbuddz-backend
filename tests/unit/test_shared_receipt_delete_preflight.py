"""Regression tests: deleting one learner must not erase another's receipt allocation."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.payments_service.routers.intents.admin import (
    _assert_receipts_are_exclusively_owned,
)


def _many(*values):
    return SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: list(values))
    )


def _single(value):
    return SimpleNamespace(scalar_one_or_none=lambda: value)


@pytest.mark.asyncio
async def test_shared_academy_receipt_blocks_member_payment_purge():
    payment = uuid.uuid4()
    receipt = uuid.uuid4()
    another_learner_allocation = uuid.uuid4()
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _many(payment),
                _many(receipt),
                _single(another_learner_allocation),
            ]
        )
    )

    with pytest.raises(HTTPException) as exc:
        await _assert_receipts_are_exclusively_owned(db, "departing-learner")
    assert exc.value.status_code == 409
    assert "other learners" in exc.value.detail
    assert db.execute.await_count == 3


@pytest.mark.asyncio
async def test_exclusive_receipt_allocation_can_pass_preflight():
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _many(uuid.uuid4()),
                _many(uuid.uuid4()),
                _single(None),
            ]
        )
    )
    await _assert_receipts_are_exclusively_owned(db, "departing-learner")


@pytest.mark.asyncio
async def test_no_member_payments_can_pass_preflight():
    db = SimpleNamespace(execute=AsyncMock(return_value=_many()))
    await _assert_receipts_are_exclusively_owned(db, "departing-learner")
    assert db.execute.await_count == 1
