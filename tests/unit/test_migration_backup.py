"""Backup connection validation must fail before touching production."""

from pathlib import Path
import runpy

import pytest

backup = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/deployment/backup_database.py")
)


def test_backup_requires_session_mode_and_preserves_encoded_password(tmp_path):
    path = tmp_path / ".env.prod"
    path.write_text(
        'IGNORED=some unrelated value\nDATABASE_SESSION_URL="postgresql+psycopg://user:p%40ss%24word@pooler.example:5432/postgres"\n'
    )
    env = backup["pg_environment"](backup["read_env"](path))
    assert env["PGPASSWORD"] == "p@ss$word"
    assert env["PGSSLMODE"] == "require"
    assert env["PGPORT"] == "5432"


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"DATABASE_TRANSACTION_URL": "postgresql://user:secret@example.com:6543/db"},
        {"DATABASE_SESSION_URL": "postgresql://user:secret@example.com:6543/db"},
        {"DATABASE_DIRECT_URL": "postgresql://user:p%0Asecret@example.com/db"},
    ],
)
def test_backup_rejects_unsafe_connection_modes(values):
    with pytest.raises(ValueError):
        backup["pg_environment"](values)
