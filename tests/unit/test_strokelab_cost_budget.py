"""StrokeLab provider budget regression tests — no external API calls."""

from types import SimpleNamespace

import pytest

from services.ai_service.providers import base


def _settings():
    return SimpleNamespace(
        STROKELAB_MAX_MODEL_CALLS_PER_ATTEMPT=2,
        STROKELAB_MAX_MODEL_COST_PER_ATTEMPT_USD=0.50,
    )


def test_strokelab_call_budget_blocks_third_call(monkeypatch):
    monkeypatch.setattr(base, "get_settings", _settings)
    token = base.start_vlm_usage_capture()
    try:
        base._enforce_strokelab_vlm_budget("gpt-4o")
        base._enforce_strokelab_vlm_budget("gpt-4o")
        with pytest.raises(RuntimeError, match="budget exhausted"):
            base._enforce_strokelab_vlm_budget("gpt-4o")
    finally:
        events = base.stop_vlm_usage_capture(token)
    assert len([e for e in events if e["kind"] == "attempt"]) == 2


def test_strokelab_spend_budget_blocks_more_calls(monkeypatch):
    monkeypatch.setattr(base, "get_settings", _settings)
    token = base.start_vlm_usage_capture()
    try:
        base._enforce_strokelab_vlm_budget("gpt-4o")
        base._record_vlm_event({"kind": "success", "cost_usd": 0.51})
        with pytest.raises(RuntimeError, match="budget exhausted"):
            base._enforce_strokelab_vlm_budget("gpt-4o")
    finally:
        base.stop_vlm_usage_capture(token)


def test_unrelated_ai_requests_not_subject_to_strokelab_budget(monkeypatch):
    monkeypatch.setattr(base, "get_settings", _settings)
    base._enforce_strokelab_vlm_budget("gpt-4o")
