from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_mock_engine

from services.sessions_service.models import Session


def test_new_migration_follows_current_sessions_head_without_backfilling_charges():
    scripts = ScriptDirectory.from_config(
        Config("services/sessions_service/alembic.ini")
    )
    assert len(scripts.get_heads()) == 1
    assert "c6e8a0b2d914" in {
        revision.revision for revision in scripts.walk_revisions()
    }
    migration = scripts.get_revision("c6e8a0b2d914")
    assert migration.down_revision == "b4d8f0a2c613"
    column = Session.__table__.c.cohort_fee_mode
    assert column.server_default.arg == "included"
    assert column.nullable is False
    statements = []
    engine = create_mock_engine(
        "postgresql://",
        lambda sql, *args, **kwargs: statements.append(
            str(sql.compile(dialect=engine.dialect))
        ),
    )
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from unittest.mock import patch

    with patch.object(
        migration.module, "op", Operations(MigrationContext.configure(engine.connect()))
    ):
        migration.module.upgrade()
    ddl = "\n".join(statements)
    assert "DEFAULT 'included' NOT NULL" in ddl
    assert "session_type = 'cohort_class'" in ddl
    assert "UPDATE session_bookings" not in ddl
    assert "UPDATE payments" not in ddl
