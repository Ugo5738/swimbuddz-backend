import asyncio
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from libs.common import middleware

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_slow_success_is_labeled_without_query_values(monkeypatch, caplog):
    monkeypatch.setattr(
        middleware, "get_settings", lambda: SimpleNamespace(SLOW_REQUEST_THRESHOLD_MS=0)
    )
    app = FastAPI()
    app.add_middleware(middleware.RequestContextMiddleware, timing_name="gateway")

    @app.get("/items/{item_id}")
    async def read_item(item_id: str):
        return middleware.Response(headers={"Server-Timing": "db;dur=1.00"})

    with caplog.at_level(logging.INFO, logger=middleware.__name__):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            response = await client.get(
                "/items/private-id?token=private-secret",
                headers={"X-Request-ID": "trace-1"},
            )
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "trace-1"
    assert response.headers["server-timing"].startswith("db;dur=1.00, gateway;dur=")
    record = next(
        record for record in caplog.records if record.message == "Slow request"
    )
    assert record.levelno == logging.WARNING
    assert record.extra_fields["route"] == "/items/{item_id}"
    assert record.extra_fields["slow_request"] is True
    assert "private-secret" not in str([r.__dict__ for r in caplog.records])
    assert "private-id" not in str([r.__dict__ for r in caplog.records])


@pytest.mark.asyncio
async def test_context_isolated_between_concurrent_requests(monkeypatch):
    monkeypatch.setattr(
        middleware,
        "get_settings",
        lambda: SimpleNamespace(SLOW_REQUEST_THRESHOLD_MS=500),
    )
    app = FastAPI()
    app.add_middleware(middleware.RequestContextMiddleware)

    @app.get("/work")
    async def work():
        from libs.common.logging import get_request_id

        await asyncio.sleep(0)
        return {"request_id": get_request_id()}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        results = await asyncio.gather(
            *(client.get("/work", headers={"X-Request-ID": str(i)}) for i in range(10))
        )
    for i, response in enumerate(results):
        assert response.json()["request_id"] == str(i)
        assert response.headers["x-request-id"] == str(i)
