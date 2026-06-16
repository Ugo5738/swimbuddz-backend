"""Branded Stroke Lab analyzer email templates (communications_service).

Confirms the analyzer guest emails render the shared branded layout (wrap_html
header/logo/footer + cta button) rather than bare inline HTML. send_email is
patched so nothing is actually delivered.
"""

import pytest

from services.communications_service.templates import analyzer

_LINK = "https://analyzer.swimbuddz.com/r/abc?guest_token=t0k"


def _capture(monkeypatch):
    cap = {}

    async def fake_send(to_email, subject, body, html_body):
        cap.update(to=to_email, subject=subject, body=body, html=html_body)
        return True

    monkeypatch.setattr(analyzer, "send_email", fake_send)
    return cap


@pytest.mark.asyncio
async def test_analyzer_ready_renders_branded(monkeypatch):
    cap = _capture(monkeypatch)
    ok = await analyzer.send_analyzer_ready_email("g@e.com", _LINK)
    assert ok is True
    assert "ready" in cap["subject"].lower()
    assert "<!DOCTYPE" in cap["html"]  # full branded document, not a fragment
    assert "logo-white" in cap["html"]  # branded header logo
    assert "SwimBuddz Limited" in cap["html"]  # branded footer
    assert "View My Analysis" in cap["html"]  # cta button
    assert _LINK in cap["html"] and _LINK in cap["body"]


@pytest.mark.asyncio
async def test_analyzer_failed_renders_branded(monkeypatch):
    cap = _capture(monkeypatch)
    ok = await analyzer.send_analyzer_failed_email("g@e.com")
    assert ok is True
    assert "<!DOCTYPE" in cap["html"]
    assert "refunded" in cap["html"].lower()
    assert "Try Another Clip" in cap["html"]
