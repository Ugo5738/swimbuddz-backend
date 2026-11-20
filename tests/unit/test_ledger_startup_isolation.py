"""Ledger must start with the packages installed in its production image."""

import os
from pathlib import Path
import subprocess
import sys


def test_ledger_starts_and_registers_intelligence_without_other_services():
    script = """
import importlib.abc
import sys

class LedgerImageOnly(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.startswith('services.') and not fullname.startswith('services.ledger_service'):
            raise ModuleNotFoundError('Package absent from Ledger image: ' + fullname)

sys.meta_path.insert(0, LedgerImageOnly())
from fastapi.testclient import TestClient
from services.ledger_service.app.main import app

with TestClient(app) as client:
    result = client.get('/health')
    assert result.status_code == 200, result.text
    assert result.json()['service'] == 'ledger'
assert '/admin/finance/intelligence' in {route.path for route in app.routes}
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        env={
            **os.environ,
            "DATABASE_URL": "postgresql+psycopg://unused@127.0.0.1:1/unused",
            "ENVIRONMENT": "development",
        },
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
