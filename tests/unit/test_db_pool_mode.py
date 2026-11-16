"""Supavisor compatibility and opt-in local connection reuse."""

from libs.db.config import should_use_null_pool


def test_shared_pooler_defaults_to_null_pool():
    assert should_use_null_pool(
        "aws-0-eu-west-1.pooler.supabase.com", local_pool_enabled=False
    )


def test_local_connection_reuse_is_opt_in_on_shared_pooler():
    assert not should_use_null_pool(
        "aws-0-eu-west-1.pooler.supabase.com", local_pool_enabled=True
    )


def test_direct_db_uses_configured_pool_either_way():
    for enabled in (False, True):
        assert not should_use_null_pool(
            "db.project.supabase.co", local_pool_enabled=enabled
        )
