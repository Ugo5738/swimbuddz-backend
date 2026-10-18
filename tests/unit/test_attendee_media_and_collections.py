"""Regression checks for attendee-derived Vault access and collection privacy."""
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.media_service.services import vault_access
from services.media_service.schemas.vault import (
    PersonalCollectionCreate, PersonalCollectionUpdate, AttendanceAccessChange,
)


def test_personal_collection_is_private_by_default():
    data = PersonalCollectionCreate(title="Freestyle progress")
    assert data.share_with_community is False
    assert PersonalCollectionUpdate(title="My swimming").share_with_community is None


def test_attendance_revocation_input():
    member = uuid.uuid4()
    data = AttendanceAccessChange(member_id=member, deny=True)
    assert data.deny is True


@pytest.mark.asyncio
async def test_absent_or_guest_does_not_get_inferred_access(monkeypatch):
    vault = SimpleNamespace(
        id=uuid.uuid4(), session_id=uuid.uuid4(),
        deleted_at=None, attendee_can_view=True, attendee_can_contribute=True,
    )
    actor = vault_access.VaultActor(uuid.uuid4(), uuid.uuid4(), False)
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    lookup = AsyncMock(return_value=[{"status": "absent", "role": "swimmer"}])
    monkeypatch.setattr(vault_access, "get_member_attendance", lookup)
    assert await vault_access.verified_attendee_role(db, vault=vault, actor=actor) is None
    lookup.return_value = [{"status": "present", "role": "guest"}]
    assert await vault_access.verified_attendee_role(db, vault=vault, actor=actor) is None


@pytest.mark.asyncio
async def test_present_member_gets_contribution_or_view_permission(monkeypatch):
    vault = SimpleNamespace(
        id=uuid.uuid4(), session_id=uuid.uuid4(),
        deleted_at=None, attendee_can_view=True, attendee_can_contribute=True,
    )
    actor = vault_access.VaultActor(uuid.uuid4(), uuid.uuid4(), False)
    db = SimpleNamespace(scalar=AsyncMock(return_value=None))
    monkeypatch.setattr(vault_access, "get_member_attendance", AsyncMock(
        return_value=[{"status": "present", "role": "swimmer"}]
    ))
    assert await vault_access.verified_attendee_role(db, vault=vault, actor=actor) == "contributor"
    vault.attendee_can_contribute = False
    assert await vault_access.verified_attendee_role(db, vault=vault, actor=actor) == "viewer"


@pytest.mark.asyncio
async def test_admin_denial_overrides_attendance(monkeypatch):
    vault = SimpleNamespace(
        id=uuid.uuid4(), session_id=uuid.uuid4(),
        deleted_at=None, attendee_can_view=True, attendee_can_contribute=True,
    )
    actor = vault_access.VaultActor(uuid.uuid4(), uuid.uuid4(), False)
    db = SimpleNamespace(scalar=AsyncMock(return_value=uuid.uuid4()))
    lookup = AsyncMock()
    monkeypatch.setattr(vault_access, "get_member_attendance", lookup)
    assert await vault_access.verified_attendee_role(db, vault=vault, actor=actor) is None
    lookup.assert_not_awaited()
