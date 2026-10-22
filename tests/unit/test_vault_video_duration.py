import subprocess
from types import SimpleNamespace

import pytest

from services.media_service.tasks import vault_previews


@pytest.mark.parametrize(
    "raw,expected",
    [("65.25\n", 65.25), ("N/A", None), ("nan", None), ("inf", None), ("-1", None)],
)
def test_probe_duration_handles_unknown_metadata(monkeypatch, raw, expected):
    monkeypatch.setattr(
        vault_previews.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=raw),
    )
    assert vault_previews._probe_duration("original.mov") == expected


def test_probe_duration_timeout_does_not_break_preview(monkeypatch):
    def timeout(*_a, **_k):
        raise subprocess.TimeoutExpired("ffprobe", 60)

    monkeypatch.setattr(vault_previews.subprocess, "run", timeout)
    assert vault_previews._probe_duration("original.mov") is None
