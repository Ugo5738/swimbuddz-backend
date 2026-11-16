"""Accounting and query-shape safeguards for Supabase traffic reductions."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from services.media_service.routers.vaults import bandwidth_summary
from services.members_service.models import Member
from services.members_service.routers._helpers import (
    member_eager_load_options,
    member_read_only_joined_options,
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_vault_bandwidth_includes_unattributed_preview_without_double_counting():
    month = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Rows([(month, 400, 1000, 250, 1000)]),
                _Rows([(month, 500, 300)]),
            ]
        ),
        scalar=AsyncMock(return_value=None),
    )

    summary = await bandwidth_summary(months=1, current_user=SimpleNamespace(), db=db)
    entry = summary.months[0]
    assert entry.upload_bytes == 400
    assert entry.download_authorized_bytes == 1000
    assert entry.download_completed_bytes == 250
    assert entry.download_reconciled_bytes == 500
    assert entry.download_pending_estimate_bytes == 1000
    # 500 matched original S3 + 300 unattributed preview S3 + 1000
    # authorized original estimate. No S3 unmatched-original double count.
    assert entry.download_effective_bytes == 1800
    assert summary.current_month_download_bytes == 1800

    source_query = db.execute.await_args_list[1].args[0]
    sql = str(
        source_query.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "vault-derivatives/%" in sql
    assert "match_status" in sql
    assert "matched" in sql


@pytest.mark.asyncio
async def test_vault_bandwidth_derivative_only_month_remains_visible():
    month = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Rows([]), _Rows([(month, 0, 750)])]),
        scalar=AsyncMock(return_value=None),
    )
    summary = await bandwidth_summary(months=1, current_user=SimpleNamespace(), db=db)
    assert len(summary.months) == 1
    assert summary.months[0].download_effective_bytes == 750
    assert summary.months[0].download_reconciled_bytes == 0
    assert summary.months[0].download_pending_estimate_bytes == 0


def test_full_member_detail_uses_one_to_one_joins():
    query = select(Member).options(*member_read_only_joined_options())
    sql = str(query.compile(dialect=postgresql.dialect()))
    for relation_table in (
        "member_profiles",
        "member_memberships",
        "member_emergency_contacts",
        "member_availabilities",
        "member_preferences",
        "coach_profiles",
    ):
        assert relation_table in sql
    assert sql.count("LEFT OUTER JOIN") >= 6


def test_locked_member_query_keeps_nullable_relations_outside_for_update():
    query = select(Member).options(*member_eager_load_options()).with_for_update()
    sql = str(query.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql
    assert "LEFT OUTER JOIN" not in sql
