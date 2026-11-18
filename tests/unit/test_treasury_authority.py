# fmt: off
"""Finance authority regression tests: approvals never imply bank execution."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.ledger_service.models.enums import LEDGER_ROLE_RANK, LedgerRole
from services.ledger_service.routers.treasury import (
    confirm_request,
    decide_request,
)
from services.ledger_service.schemas.treasury import ConfirmRequest, Decision


def _request_context():
    return SimpleNamespace(state=SimpleNamespace(org_id=uuid4()))


def _session(*scalar_results):
    return SimpleNamespace(
        scalar=AsyncMock(side_effect=list(scalar_results)),
        add=Mock(),
        commit=AsyncMock(),
        refresh=AsyncMock(),
    )


def test_operations_role_cannot_post_manual_journals():
    assert LEDGER_ROLE_RANK[LedgerRole.VIEWER] < LEDGER_ROLE_RANK[LedgerRole.OPERATIONS]
    assert LEDGER_ROLE_RANK[LedgerRole.OPERATIONS] < LEDGER_ROLE_RANK[LedgerRole.ACCOUNTANT]


@pytest.mark.asyncio
async def test_requestor_cannot_approve_own_expense():
    actor = SimpleNamespace(id=uuid4())
    item = SimpleNamespace(id=uuid4(), status="requested", requested_by=actor.id)
    session = _session(item)
    with pytest.raises(HTTPException) as exc:
        await decide_request(
            item.id, Decision(action="approve", reason="Urgent pool repair approved as a documented exception"), _request_context(),
            actor=actor, session=session,
        )
    assert exc.value.status_code == 403
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_approval_is_internal_only_not_paid():
    actor = SimpleNamespace(id=uuid4())
    item = SimpleNamespace(
        id=uuid4(), status="requested", requested_by=uuid4(),
        budget_id=None, approved_by=None, approved_at=None, rejected_reason=None, approval_note=None,
    )
    session = _session(item)
    await decide_request(
        item.id, Decision(action="approve", reason="Emergency pool repair: owner-authorised exception"), _request_context(),
        actor=actor, session=session,
    )
    assert item.status == "approved"
    assert item.approved_by == actor.id
    assert not hasattr(item, "bank_line_id")
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_approved_request_cannot_be_confirmed_without_bank_debit():
    actor = SimpleNamespace(id=uuid4())
    item = SimpleNamespace(
        id=uuid4(), status="approved", requested_by=uuid4(),
        source_bank_id=uuid4(), amount_minor=150_000,
    )
    session = _session(item, None)
    with pytest.raises(HTTPException) as exc:
        await confirm_request(
            item.id, ConfirmRequest(source_line_id=uuid4()),
            _request_context(), actor=actor, session=session,
        )
    assert exc.value.status_code == 422
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_unbudgeted_approval_requires_reason():
    actor = SimpleNamespace(id=uuid4())
    item = SimpleNamespace(id=uuid4(), status="requested", requested_by=uuid4(), budget_id=None)
    session = _session(item)
    with pytest.raises(HTTPException) as exc:
        await decide_request(item.id, Decision(action="approve"), _request_context(), actor=actor, session=session)
    assert exc.value.status_code == 422
    session.commit.assert_not_awaited()
