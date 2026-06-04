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
from typing import Optional

import httpx
from supabase import Client, create_client

from libs.common.config import get_settings
from libs.common.logging import get_logger

logger = get_logger(__name__)


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


async def invite_user_by_email(
    email: str,
    *,
    redirect_to: Optional[str] = None,
    data: Optional[dict] = None,
) -> dict:
    """Invite a user by email via the Supabase Admin API (GoTrue ``/invite``).

    Creates the auth user if absent and emails them an invite link to set a
    password (landing on ``redirect_to``). Grants NO app_metadata roles — scoped
    access (e.g. a finance-team membership) is the caller's responsibility.

    Best-effort: the caller must NOT roll back its own work if this fails — it
    returns a status to surface instead. One of:
      ``{"status": "invited"}``           — invite email sent
      ``{"status": "exists"}``            — already has a login (no email; they sign in)
      ``{"status": "error", "detail":…}`` — request failed (caller falls back to manual)
    """
    settings = get_settings()
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_ROLE_KEY:
        return {"status": "error", "detail": "Supabase admin not configured"}

    headers = {
        "apikey": settings.SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{settings.SUPABASE_URL}/auth/v1/invite"
    params = {"redirect_to": redirect_to} if redirect_to else None
    payload: dict = {"email": email}
    if data:
        payload["data"] = data

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload, headers=headers, params=params)
    except httpx.HTTPError as exc:
        logger.warning("Supabase invite request failed for %s: %s", email, exc)
        return {"status": "error", "detail": str(exc)}

    if resp.status_code in (200, 201):
        return {"status": "invited"}

    body = resp.text.lower()
    if resp.status_code in (400, 409, 422) and (
        "already" in body or "registered" in body or "exists" in body
    ):
        return {"status": "exists"}

    logger.warning(
        "Supabase invite for %s returned %s: %s", email, resp.status_code, resp.text
    )
    return {"status": "error", "detail": f"{resp.status_code}: {resp.text[:200]}"}
