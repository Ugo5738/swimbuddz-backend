"""Prevent SQLAlchemy bind parsing from corrupting the media vault JSONB index."""

from sqlalchemy.schema import CreateIndex
from sqlalchemy.dialects import postgresql

from services.media_service.models.vault import MediaVault


def test_primary_member_upload_index_compiles_without_json_bind_parameters():
    index = next(
        index
        for index in MediaVault.__table__.indexes
        if index.name == "uq_media_vaults_primary_member_upload"
    )
    compiled = CreateIndex(index).compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "settings_json ->> 'member_self_upload' = 'true'" in sql
    assert "NULL" not in sql
    assert not compiled.params


def test_migration_predicate_matches_model_predicate():
    """Migration index and ORM schema must never diverge."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    migration = (
        root
        / "services/media_service/alembic/versions"
        / "f18a47d20311_attendee_media_and_collections.py"
    ).read_text()
    predicate = "owner_member_id IS NOT NULL AND settings_json ->> 'member_self_upload' = 'true'"
    assert predicate in migration
