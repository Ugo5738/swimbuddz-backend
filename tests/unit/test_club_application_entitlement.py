from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from services.payments_service.routers.intents._entitlement import _club


class _Response:
    status_code = 200
    text = "ok"


class _AsyncClientContext:
    def __init__(self, client):
        self.client = client

    async def __aenter__(self):
        return self.client

    async def __aexit__(self, *_args):
        return None


def _payment(*, application_id: str | None):
    metadata = {
        "months": 6,
        "community_extension_months": 12,
        "components_kobo": {"community_experience": 3_000_000},
        "community_experience_selected": True,
    }
    if application_id:
        metadata["club_application_id"] = application_id
    return SimpleNamespace(
        id=uuid4(),
        member_auth_id="auth-ay",
        reference="PAY-AY-CLUB",
        paid_at=None,
        payment_metadata=metadata,
    )


@pytest.mark.asyncio
async def test_approved_application_activates_only_dated_enrollments(monkeypatch):
    """New Club checkout never re-runs legacy tier/readiness activation."""
    client = SimpleNamespace(post=AsyncMock(return_value=_Response()))
    monkeypatch.setattr(
        _club.httpx,
        "AsyncClient",
        lambda **_kwargs: _AsyncClientContext(client),
    )

    await _club.apply_club(_payment(application_id="application-ay"))

    called_urls = [call.args[0] for call in client.post.await_args_list]
    assert any(url.endswith("/community/extend") for url in called_urls)
    assert any(
        url.endswith("/clubs/internal/applications/application-ay/activate")
        for url in called_urls
    )
    assert not any(url.endswith("/club/activate") for url in called_urls)


@pytest.mark.asyncio
async def test_legacy_checkout_keeps_the_legacy_activation_path(monkeypatch):
    client = SimpleNamespace(post=AsyncMock(return_value=_Response()))
    monkeypatch.setattr(
        _club.httpx,
        "AsyncClient",
        lambda **_kwargs: _AsyncClientContext(client),
    )

    await _club.apply_club(_payment(application_id=None))

    called_urls = [call.args[0] for call in client.post.await_args_list]
    assert any(url.endswith("/club/activate") for url in called_urls)
    assert not any("/clubs/internal/applications/" in url for url in called_urls)
