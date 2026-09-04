"""Admin referral-code support for attributed guest self-payment links."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.wallet_service.routers import referral_admin
from services.wallet_service.services import referral_service


@pytest.mark.asyncio
async def test_admin_can_get_member_code_for_guest_link(monkeypatch):
    expires_at = datetime(2026, 12, 2, tzinfo=timezone.utc)
    get_code = AsyncMock(
        return_value=SimpleNamespace(
            member_auth_id="member-auth-1",
            code="PETER10",
            is_active=True,
            expires_at=expires_at,
        )
    )
    monkeypatch.setattr(referral_admin, "get_or_create_referral_code", get_code)
    db = AsyncMock()

    response = await referral_admin.get_member_referral_code(
        "member-auth-1",
        current_user=SimpleNamespace(user_id="admin-auth-1"),
        db=db,
    )

    get_code.assert_awaited_once_with("member-auth-1", db)
    assert response.member_auth_id == "member-auth-1"
    assert response.code == "PETER10"
    assert response.is_active is True
    assert response.expires_at == expires_at


@pytest.mark.asyncio
async def test_expired_exhausted_member_code_is_renewed_in_place(monkeypatch):
    now = datetime(2026, 9, 3, tzinfo=timezone.utc)
    code = SimpleNamespace(
        member_auth_id="member-auth-1",
        code="PETER10",
        is_active=True,
        expires_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
        uses_count=50,
        max_uses=50,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = code
    db = AsyncMock()
    db.execute.side_effect = [result, result]
    monkeypatch.setattr(referral_service, "utc_now", lambda: now)

    renewed = await referral_service.get_or_create_referral_code(
        "member-auth-1",
        db,
    )

    assert renewed is code
    assert renewed.expires_at == datetime(2026, 12, 2, tzinfo=timezone.utc)
    assert renewed.max_uses == 100
    db.commit.assert_awaited_once()
    db.refresh.assert_awaited_once_with(code)
