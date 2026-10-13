"""Deployment targets `head`, so each service must have exactly one head."""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SERVICE_CONFIGS = sorted((PROJECT_ROOT / "services").glob("*/alembic.ini"))


@pytest.mark.parametrize("config_path", SERVICE_CONFIGS, ids=lambda p: p.parent.name)
def test_service_has_one_migration_head(config_path):
    config = Config(str(config_path))
    config.set_main_option("script_location", str(config_path.parent / "alembic"))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1, (
        f"{config_path.parent.name} has {len(heads)} migration heads: {heads}. "
        "Generate an Alembic merge revision before deploying."
    )
