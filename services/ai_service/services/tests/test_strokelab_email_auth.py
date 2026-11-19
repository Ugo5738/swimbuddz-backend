"""Email ownership proof must not authorize a different paid-credit account."""
import re
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from services.ai_service.services import email_auth


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def get(self, key):
        return self.data.get(key)

    async def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)

    async def incr(self, key):
        value = int(self.data.get(key, 0)) + 1
        self.data[key] = value
        return value

    async def expire(self, key, seconds):
        return bool(key in self.data)


@pytest.mark.asyncio
async def test_verified_code_authorizes_only_matching_email(monkeypatch):
    redis = FakeRedis()

    @asynccontextmanager
    async def fake_redis():
        yield redis

    sent = AsyncMock(return_value=True)
    monkeypatch.setattr(email_auth, "_redis", fake_redis)
    monkeypatch.setattr(email_auth, "get_email_client", lambda: MagicMock(send=sent))

    await email_auth.request_email_code("Buyer+alias@Gmail.com")
    sent.assert_awaited_once()
    body = sent.call_args.kwargs["body"]
    code = re.search(r"\b\d{6}\b", body).group(0)
    token = await email_auth.verify_email_code("buyer+alias@gmail.com", code)

    await email_auth.require_paid_credit_owner("buyer@gmail.com", token)
    with pytest.raises(HTTPException) as exc:
        await email_auth.require_paid_credit_owner("other@gmail.com", token)
    assert exc.value.status_code == 403

    with pytest.raises(HTTPException) as replay:
        await email_auth.verify_email_code("buyer+alias@gmail.com", code)
    assert replay.value.status_code == 400
