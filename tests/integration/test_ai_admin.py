"""Integration tests for ai_service — admin config + prompt invariants.

The scoring endpoints (/ai/score/*) call live LLM providers and belong
with a provider-mocked suite. What's pure business logic — and worth
pinning — is the admin plane:

  - model configs hold a *single-default* invariant: creating a config
    with is_default=True must demote every other default
  - prompt templates are *versioned per name*: a new template for an
    existing name auto-increments the version and deactivates the prior
    active version (so exactly one version is ever active)
  - the AI-request log lists with pagination + a request_type filter

All admin-gated; `_wire_app` overrides require_admin, so `ai_client`
exercises them directly. The shared test DB may carry seed configs, so
assertions scope to rows this test created (unique names / ids).

Not in scope (follow-up): the three /ai/score/* endpoints (need the LLM
provider + scoring engine mocked), prompt rollback, cost rollups.
"""

import uuid

import pytest

# Admin router is mounted at /ai + /admin (see app/main.py).
_MODELS = "/ai/admin/models"
_PROMPTS = "/ai/admin/prompts"
_REQUESTS = "/ai/admin/requests"


def _model_payload(**overrides):
    s = uuid.uuid4().hex[:6]
    d = {
        "provider": "anthropic",
        "model_name": f"claude-test-{s}",
        "is_enabled": True,
        "is_default": False,
        "max_tokens": 4096,
        "temperature": 0.1,
    }
    d.update(overrides)
    return d


def _prompt_payload(name, **overrides):
    d = {
        "name": name,
        "system_prompt": "You are a scorer.",
        "user_prompt_template": "Score this: {input}",
        "output_schema": {"type": "object"},
    }
    d.update(overrides)
    return d


def _make_ai_request(request_type, **overrides):
    from services.ai_service.models import AIRequest

    d = {
        "id": uuid.uuid4(),
        "request_type": request_type,
        "model_provider": "anthropic",
        "model_name": "claude-test",
        "input_data": {"x": 1},
        "status": "success",
    }
    d.update(overrides)
    return AIRequest(**d)


# ---------------------------------------------------------------------------
# model config — single-default invariant
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_create_model_config_roundtrip(ai_client):
    resp = await ai_client.post(_MODELS, json=_model_payload())
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "anthropic"
    assert body["is_default"] is False
    assert body["id"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_new_default_demotes_previous_default(ai_client):
    """Creating a second is_default=True config must demote the first."""
    a = await ai_client.post(_MODELS, json=_model_payload(is_default=True))
    assert a.status_code == 200, a.text
    a_id = a.json()["id"]
    assert a.json()["is_default"] is True

    b = await ai_client.post(_MODELS, json=_model_payload(is_default=True))
    assert b.status_code == 200, b.text
    b_id = b.json()["id"]

    listing = await ai_client.get(_MODELS)
    assert listing.status_code == 200, listing.text
    by_id = {c["id"]: c for c in listing.json()}
    assert by_id[a_id]["is_default"] is False  # demoted
    assert by_id[b_id]["is_default"] is True  # the new sole default


# ---------------------------------------------------------------------------
# prompt template — per-name versioning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_prompt_versions_autoincrement_and_deactivate_prior(ai_client):
    name = f"scorer_{uuid.uuid4().hex[:8]}"

    v1 = await ai_client.post(_PROMPTS, json=_prompt_payload(name))
    assert v1.status_code == 200, v1.text
    assert v1.json()["version"] == 1
    assert v1.json()["is_active"] is True

    v2 = await ai_client.post(_PROMPTS, json=_prompt_payload(name))
    assert v2.status_code == 200, v2.text
    assert v2.json()["version"] == 2
    assert v2.json()["is_active"] is True

    # Exactly one active version for this name, and it's v2.
    listing = await ai_client.get(_PROMPTS)
    assert listing.status_code == 200, listing.text
    mine = [t for t in listing.json() if t["name"] == name]
    assert sorted(t["version"] for t in mine) == [1, 2]
    active = [t for t in mine if t["is_active"]]
    assert len(active) == 1
    assert active[0]["version"] == 2


# ---------------------------------------------------------------------------
# AI-request log listing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_requests_filters_by_type_and_paginates(ai_client, db_session):
    rt = f"itest_{uuid.uuid4().hex[:8]}"
    db_session.add_all([_make_ai_request(rt), _make_ai_request(rt)])
    await db_session.commit()

    resp = await ai_client.get(f"{_REQUESTS}?request_type={rt}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Unique request_type → exactly the two rows we seeded.
    assert body["total"] == 2
    assert len(body["items"]) == 2
    assert {item["request_type"] for item in body["items"]} == {rt}
    assert "page" in body and "page_size" in body


@pytest.mark.asyncio
@pytest.mark.integration
async def test_list_requests_unknown_type_is_empty(ai_client):
    resp = await ai_client.get(
        f"{_REQUESTS}?request_type=does-not-exist-{uuid.uuid4().hex}"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []
