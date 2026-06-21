"""No-API tests for the inspect idempotency helper (the free-on-re-view guard)."""

from __future__ import annotations

from types import SimpleNamespace

from services.ai_service.services.drilldown import existing_inspect_finding


def _row(coach_result):
    return SimpleNamespace(coach_result=coach_result)


def test_existing_inspect_finding_matches_area_and_instance():
    row = _row(
        {
            "result": {
                "results": [
                    {
                        "component": "recovery_coach",
                        "findings": [
                            {
                                "area": "recovery_elbow",
                                "instance_id": 0,
                                "observation": "a",
                            },
                            {
                                "area": "recovery_elbow",
                                "instance_id": 3,
                                "observation": "b",
                            },
                        ],
                    }
                ]
            }
        }
    )
    found = existing_inspect_finding(row, "recovery_elbow", 3)
    assert found is not None and found["observation"] == "b"  # re-view → $0
    assert existing_inspect_finding(row, "recovery_elbow", 7) is None  # not coached yet
    assert existing_inspect_finding(row, "body_line", 3) is None  # different aspect
    assert existing_inspect_finding(_row(None), "recovery_elbow", 0) is None  # no run
