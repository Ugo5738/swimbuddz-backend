"""Store must boot alone while Finance remains the authority for approvals."""

import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from libs.auth.models import AuthUser
from services.store_service.routers import procurement_access as access


def test_store_starts_and_health_responds_without_other_service_packages():
    script = """
import importlib.abc
import sys
class StoreImageOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('services.') and not fullname.startswith('services.store_service'):
            raise ModuleNotFoundError('Other service absent from Store image: ' + fullname)
sys.meta_path.insert(0, StoreImageOnly())
from services.store_service.app.main import app
from fastapi.testclient import TestClient
with TestClient(app) as client:
    result = client.get('/health')
    assert result.status_code == 200, result.text
    assert result.json()['service'] == 'store'
assert '/store/products' in {route.path for route in app.routes}
assert '/admin/store/procurement/orders' in {route.path for route in app.routes}
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "DATABASE_URL": "postgresql+psycopg://unused@127.0.0.1:1/unused",
            "ENVIRONMENT": "development",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def actor(role="authenticated"):
    return AuthUser(
        user_id="signed-in-user", role=role, app_metadata={"roles": ["admin"]}
    )


def request(token="Bearer actual-caller-token"):
    return Request(
        {
            "type": "http",
            "headers": [(b"authorization", token.encode())] if token else [],
        }
    )


def owner_response(**values):
    return {
        "id": str(uuid.uuid4()),
        "role": "owner",
        "auth_id": "signed-in-user",
        "deactivated_at": None,
        **values,
    }


def mock_ledger(monkeypatch, payload=None, status=200, failure=None, content=None):
    response = httpx.Response(
        status,
        json=payload if payload is not None else owner_response(),
        request=httpx.Request("GET", "http://ledger/admin/finance/users/me"),
    )
    if content is not None:
        response = httpx.Response(
            status,
            content=content,
            request=httpx.Request("GET", "http://ledger/admin/finance/users/me"),
        )
    get = AsyncMock(return_value=response, side_effect=failure)
    client = AsyncMock()
    client.__aenter__.return_value = SimpleNamespace(get=get)
    monkeypatch.setattr(access.httpx, "AsyncClient", lambda **kwargs: client)
    monkeypatch.setattr(
        access,
        "get_settings",
        lambda: SimpleNamespace(LEDGER_SERVICE_URL="http://ledger"),
    )
    return get


async def test_owner_authority_is_resolved_remotely_with_the_callers_token(monkeypatch):
    payload = owner_response()
    get = mock_ledger(monkeypatch, payload)
    owner = await access.procurement_owner(request(), actor())
    assert owner.id == uuid.UUID(payload["id"])
    assert owner.auth_id == "signed-in-user"
    get.assert_awaited_once_with(
        "http://ledger/admin/finance/users/me",
        headers={"Authorization": "Bearer actual-caller-token"},
    )


async def test_email_linked_owner_uses_authenticated_identity_for_independent_approval(
    monkeypatch,
):
    mock_ledger(monkeypatch, owner_response(auth_id=None))
    owner = await access.procurement_owner(request(), actor())
    assert owner.auth_id == "signed-in-user"
    from services.store_service.routers.procurement import decide_purchase_order

    db = SimpleNamespace(
        scalar=AsyncMock(
            return_value=SimpleNamespace(
                status="submitted", requested_by="signed-in-user"
            )
        ),
        commit=AsyncMock(),
    )
    with pytest.raises(HTTPException) as error:
        await decide_purchase_order(uuid.uuid4(), True, owner, db)
    assert error.value.status_code == 403
    db.commit.assert_not_awaited()


@pytest.mark.parametrize(
    "role", ["viewer", "operations", "accountant", "admin", "procurement_officer"]
)
async def test_application_admin_cannot_substitute_for_finance_owner(monkeypatch, role):
    mock_ledger(monkeypatch, owner_response(role=role))
    with pytest.raises(HTTPException) as error:
        await access.procurement_owner(request(), actor())
    assert error.value.status_code == 403


async def test_inactive_owner_is_denied(monkeypatch):
    mock_ledger(monkeypatch, owner_response(deactivated_at="2026-11-19T00:00:00Z"))
    with pytest.raises(HTTPException) as error:
        await access.procurement_owner(request(), actor())
    assert error.value.status_code == 403


@pytest.mark.parametrize("status", [401, 403, 500, 503, 302])
async def test_finance_denial_and_unavailability_fail_closed(monkeypatch, status):
    mock_ledger(monkeypatch, status=status)
    with pytest.raises(HTTPException) as error:
        await access.procurement_owner(request(), actor())
    assert error.value.status_code == (status if status in {401, 403} else 503)


async def test_service_role_does_not_bypass_finance_membership(monkeypatch):
    get = mock_ledger(monkeypatch, status=403)
    with pytest.raises(HTTPException) as error:
        await access.procurement_owner(
            request("Bearer service-caller-token"), actor("service_role")
        )
    assert error.value.status_code == 403
    get.assert_awaited_once()


async def test_network_failure_denies_protected_action(monkeypatch):
    mock_ledger(monkeypatch, failure=httpx.ConnectError("Finance unavailable"))
    with pytest.raises(HTTPException) as error:
        await access.procurement_owner(request(), actor())
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "payload", [{"role": "owner"}, owner_response(id="invalid"), ["owner"]]
)
async def test_malformed_membership_fails_closed(monkeypatch, payload):
    mock_ledger(monkeypatch, payload)
    with pytest.raises(HTTPException) as error:
        await access.procurement_owner(request(), actor())
    assert error.value.status_code == 503


async def test_invalid_json_fails_closed(monkeypatch):
    mock_ledger(monkeypatch, content=b"upstream returned html")
    with pytest.raises(HTTPException) as error:
        await access.procurement_owner(request(), actor())
    assert error.value.status_code == 503


async def test_missing_bearer_token_does_not_call_finance(monkeypatch):
    get = mock_ledger(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await access.procurement_owner(request(None), actor())
    assert error.value.status_code == 401
    get.assert_not_awaited()
