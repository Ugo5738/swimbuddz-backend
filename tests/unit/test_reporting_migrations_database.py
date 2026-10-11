"""Exercise real Alembic upgrades in disposable localhost PostgreSQL schemas."""

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORTING_CONFIG = Config(str(PROJECT_ROOT / "services/reporting_service/alembic.ini"))
REPORTING_CONFIG.set_main_option(
    "script_location", str(PROJECT_ROOT / "services/reporting_service/alembic")
)
TEST_URL = os.environ.get("REPORTING_MIGRATION_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="Requires a disposable localhost PostgreSQL database"
)


@pytest.fixture
def migration_database():
    url = make_url(TEST_URL)
    assert url.host in {"localhost", "127.0.0.1"}, "Local validation database only"
    schema = "reporting_migration_" + uuid.uuid4().hex
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    isolated_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    isolated_engine = create_engine(isolated_url)
    try:
        yield isolated_engine, isolated_url.render_as_string(hide_password=False)
    finally:
        isolated_engine.dispose()
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        engine.dispose()


def upgrade(url, revision):
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "services/reporting_service/alembic.ini",
            "upgrade",
            revision,
        ],
        cwd=PROJECT_ROOT,
        env={**os.environ, "DATABASE_URL": url, "ENV_FILE": "/dev/null"},
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.mark.parametrize("previous_head", [None, "a08e16d7c9fa", "d4e8f1a6b209"])
def test_reporting_upgrade_preserves_both_branches(migration_database, previous_head):
    engine, url = migration_database
    snapshot_id = uuid.uuid4()
    if previous_head:
        upgrade(url, previous_head)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO quarterly_snapshots "
                    "(id, year, quarter, status, member_count, created_at) "
                    "VALUES (:id, 2026, 3, 'completed', 10, now())"
                ),
                {"id": snapshot_id},
            )

    upgrade(url, "head")
    upgrade(url, "head")  # A deployment retry must be safe as well.
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT version_num FROM alembic_version_reporting")
        ).scalars().all() == [
            ScriptDirectory.from_config(REPORTING_CONFIG).get_current_head()
        ]
        connection.execute(text("SELECT * FROM content_acquisition_snapshots LIMIT 0"))
        connection.execute(
            text("SELECT activity_details FROM member_quarterly_reports LIMIT 0")
        )
        connection.execute(
            text("SELECT ecosystem_stats FROM community_quarterly_stats LIMIT 0")
        )
        if previous_head:
            assert (
                connection.execute(
                    text("SELECT member_count FROM quarterly_snapshots WHERE id = :id"),
                    {"id": snapshot_id},
                ).scalar_one()
                == 10
            )
