"""Regression tests for the lightweight automatic fulfillment retry scan."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from services.payments_service import tasks


@pytest.mark.asyncio
async def test_retry_query_excludes_dead_letters_before_loading_rows(monkeypatch):
    """Do not fetch terminal dead letters on every five-minute worker run."""
    executed = []

    class Result:
        def scalars(self):
            return self

        def all(self):
            return []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        async def execute(self, stmt):
            executed.append(stmt)
            return Result()

    monkeypatch.setattr(tasks, "AsyncSessionLocal", Session)

    await tasks.retry_failed_entitlement_fulfillment()

    assert len(executed) == 1
    compiled = executed[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "coalesce" in sql.lower()
    assert "entitlement_applied_at IS NULL" in sql
    assert "dead_letter" in compiled.params.values()
    # Missing fulfillment metadata remains eligible (coalesce -> empty string).
    assert "" in compiled.params.values()
