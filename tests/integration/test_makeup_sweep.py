"""Smoke test for the make-up completion sweep.

The sweep uses its own AsyncSessionLocal and reads attendance over HTTP. Here we
mock the attendance fetch to return nothing, so the sweep runs its query +
orchestration end-to-end without flipping anything or making real HTTP calls.
The decision logic itself is unit-tested in test_makeup_scheduling.py.
"""

import pytest

import services.sessions_service.tasks as tasks_mod

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_sweep_runs_clean(monkeypatch):
    async def _none(member_id, *, session_ids=None, calling_service):
        return []

    monkeypatch.setattr(tasks_mod, "get_member_attendance", _none)

    result = await tasks_mod.sweep_complete_makeups()
    assert set(result) >= {"checked", "completed", "forfeited"}
    # no attendance records returned → nothing flips
    assert result["completed"] == 0
    assert result["forfeited"] == 0
