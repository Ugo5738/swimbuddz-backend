"""Canaries stay bounded, independent, Supavisor-compatible and default-off."""

from pathlib import Path
import json
import runpy
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("enabled", [False, True])
def test_canary_engine_preserves_prepared_statement_and_connection_guards(enabled):
    settings = SimpleNamespace(
        DATABASE_URL="postgresql+psycopg://test:test@aws-1-test.pooler.supabase.com:6543/db",
        ENVIRONMENT="production",
        DB_USE_LOCAL_POOL=enabled,
        DB_POOL_SIZE=1,
        DB_MAX_OVERFLOW=0,
        DB_POOL_TIMEOUT=30,
        DB_POOL_RECYCLE=1800,
        DB_APPLICATION_NAME="swimbuddz-members-api",
    )
    with (
        patch("libs.common.config.get_settings", return_value=settings),
        patch("sqlalchemy.ext.asyncio.create_async_engine") as create_engine,
        patch("sqlalchemy.ext.asyncio.async_sessionmaker"),
    ):
        runpy.run_path(str(ROOT / "libs/db/config.py"))
    options = create_engine.call_args.kwargs
    assert options["pool_pre_ping"] is True
    assert options["connect_args"]["prepare_threshold"] is None
    assert options["connect_args"]["sslmode"] == "require"
    assert options["connect_args"]["application_name"] == settings.DB_APPLICATION_NAME
    if enabled:
        assert options["pool_size"] == 1 and options["max_overflow"] == 0
        assert "poolclass" not in options
    else:
        from sqlalchemy.pool import NullPool

        assert options["poolclass"] is NullPool


def test_compose_canaries_are_api_only_and_keep_ai_credentials_boundaries():
    services = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text())[
        "services"
    ]
    for service, switch, name in (
        ("members-service", "MEMBERS_API_DB_USE_LOCAL_POOL", "swimbuddz-members-api"),
        ("chat-service", "CHAT_API_DB_USE_LOCAL_POOL", "swimbuddz-chat-api"),
    ):
        env = services[service]["environment"]
        assert env["DB_USE_LOCAL_POOL"] == "${" + switch + ":-false}"
        assert env["DB_POOL_SIZE"] == "1" and env["DB_MAX_OVERFLOW"] == "0"
        assert env["DB_APPLICATION_NAME"] == name
        assert env["ANTHROPIC_API_KEY"] == env["GEMINI_API_KEY"] == ""
        for other, config in services.items():
            if other != service:
                assert switch not in str(config.get("environment", {}))
    assert services["members-service"]["environment"]["OPENAI_API_KEY"] == ""
    # Chat's legacy moderation-only OpenAI fallback must remain available.
    assert "OPENAI_API_KEY" not in services["chat-service"]["environment"]
    assert (
        services["media-worker"]["environment"]["DB_USE_LOCAL_POOL"]
        == "${MEDIA_WORKER_DB_USE_LOCAL_POOL:-false}"
    )


def test_both_deploy_paths_forward_default_off_switches_and_capture_rollback(
    tmp_path, monkeypatch
):
    for filename in ("deploy.yml", "deploy-server.yml"):
        workflow = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
        step = workflow["jobs"]["deploy"]["steps"][0]
        for switch in ("MEMBERS_API_DB_USE_LOCAL_POOL", "CHAT_API_DB_USE_LOCAL_POOL"):
            assert "'false'" in step["env"][switch]
            assert switch in step["with"]["envs"].split(",")
    capture = runpy.run_path(str(ROOT / "scripts/deployment/capture_recovery.py"))[
        "capture"
    ]
    monkeypatch.setenv("MEMBERS_API_DB_USE_LOCAL_POOL", "true")
    monkeypatch.setenv("CHAT_API_DB_USE_LOCAL_POOL", "false")
    repo = tmp_path / "repo"
    repo.mkdir()
    destination = tmp_path / "recovery"
    running = [
        {
            "Config": {
                "Labels": {"com.docker.compose.service": "members-service"},
                "Env": ["DB_USE_LOCAL_POOL=false"],
            },
            "Image": "test-image",
        }
    ]
    with (
        patch("subprocess.check_output", side_effect=["test-id", json.dumps(running)]),
        patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=0, stdout="previous-commit"),
        ),
    ):
        capture(repo, destination, "test", "docker-compose.prod.yml")
    saved = (destination / "compose-shell.env").read_text()
    assert "MEMBERS_API_DB_USE_LOCAL_POOL='false'" in saved
    assert "CHAT_API_DB_USE_LOCAL_POOL='false'" in saved
