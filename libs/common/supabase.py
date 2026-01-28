"""Centralized Supabase client factory.

Provides singleton instances of Supabase clients for consistent configuration
and easier testing.

Usage:
    from libs.common.supabase import get_supabase_client, get_supabase_admin_client

    # For user-facing operations (uses anon key)
    supabase = get_supabase_client()

    # For admin operations (uses service role key)
    admin_supabase = get_supabase_admin_client()
"""

from functools import lru_cache

from supabase import Client, create_client

from libs.common.config import get_settings


@lru_cache(maxsize=1)
def get_supabase_client() -> Client:
    """
    Get Supabase client with anon key.

    Use for user-facing operations where RLS policies apply.
    Client is cached and reused across requests.
    """
    settings = get_settings()
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


@lru_cache(maxsize=1)
def get_supabase_admin_client() -> Client:
    """
    Get Supabase client with service role key.

    Use for admin operations that bypass RLS:
    - User management (password reset, metadata updates)
    - Direct database access
    - Storage operations

    Client is cached and reused across requests.
    """
    settings = get_settings()
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_SERVICE_ROLE_KEY)
