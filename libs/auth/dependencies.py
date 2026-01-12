import time
from datetime import datetime, timezone
from typing import Annotated, Any, Dict, Optional

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from pydantic import ValidationError

settings = get_settings()


def _service_role_jwt(service_name: str = "internal") -> str:
    """
    Generate a short-lived service role JWT for service-to-service communication.

    Args:
        service_name: Name of the calling service (for audit purposes)

    Returns:
        JWT token string valid for 60 seconds
    """
    now = int(datetime.now(tz=timezone.utc).timestamp())
    payload = {
        "sub": f"service:{service_name}",
        "email": settings.ADMIN_EMAIL if hasattr(settings, "ADMIN_EMAIL") else None,
        "role": "service_role",
        "iat": now,
        "exp": now + 60,
    }
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


security = HTTPBearer()
_JWKS_CACHE: dict[str, Any] = {"keys": None, "fetched_at": 0}
_JWKS_TTL_SECONDS = 300


async def _get_jwk_for_kid(kid: str) -> Optional[Dict[str, Any]]:
    """
    Fetch the Supabase JWKS and return the key matching the given kid.
    Caches keys for a short TTL to avoid repeated network calls.
    """
    now = time.time()
    if (
        _JWKS_CACHE["keys"] is None
        or now - _JWKS_CACHE["fetched_at"] > _JWKS_TTL_SECONDS
    ):
        jwks_url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        try:
            headers = {"apikey": settings.SUPABASE_ANON_KEY}
            async with httpx.AsyncClient(timeout=5) as client:
                res = await client.get(jwks_url, headers=headers)
                res.raise_for_status()
                data = res.json()
                _JWKS_CACHE["keys"] = data.get("keys", [])
                _JWKS_CACHE["fetched_at"] = now
        except Exception:
            _JWKS_CACHE["keys"] = None
            _JWKS_CACHE["fetched_at"] = now
            return None

    keys = _JWKS_CACHE["keys"] or []
    for key in keys:
        if key.get("kid") == kid:
            return key
    return None


async def get_current_user(
    token: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> AuthUser:
    """
    Validate Supabase JWT and return the authenticated user.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        header = jwt.get_unverified_header(token.credentials)
        alg = header.get("alg", "HS256")

        # Supabase may sign user tokens with ES256/RS256; fetch JWKs when needed.
        if alg.startswith("HS"):
            key = settings.SUPABASE_JWT_SECRET
            algorithms = ["HS256"]
        else:
            kid = header.get("kid")
            jwk_key = await _get_jwk_for_kid(kid) if kid else None
            if not jwk_key:
                raise credentials_exception
            key = jwk_key
            algorithms = [alg]

        # First try with audience verification (Supabase user tokens use "authenticated")
        try:
            payload = jwt.decode(
                token.credentials,
                key,
                algorithms=algorithms,
                audience="authenticated",
                options={"verify_aud": True},
            )
        except JWTError as e:
            # If audience verification fails, try without it for service tokens
            # that may not have the audience claim
            if "audience" in str(e).lower() or "aud" in str(e).lower():
                payload = jwt.decode(
                    token.credentials,
                    key,
                    algorithms=algorithms,
                    options={"verify_aud": False},
                )
            else:
                raise

        user = AuthUser(**payload)
        return user

    except (JWTError, ValidationError):
        raise credentials_exception


async def require_admin(
    current_user: Annotated[AuthUser, Depends(get_current_user)],
) -> AuthUser:
    """
    Ensure the user has the 'admin' role (or equivalent claim).
    Checks:
    - app_metadata.roles contains "admin"
    - token role is "service_role"
    - email is in configured ADMIN_EMAILS
    """
    is_service_role = current_user.role == "service_role"
    has_admin_role = current_user.has_role("admin")
    is_whitelisted_email = current_user.email is not None and current_user.email in (
        settings.ADMIN_EMAILS or []
    )

    if not (is_service_role or has_admin_role or is_whitelisted_email):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return current_user


# Optional bearer security - allows missing token
optional_security = HTTPBearer(auto_error=False)


async def get_optional_user(
    token: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(optional_security)
    ],
) -> Optional[AuthUser]:
    """
    Optionally validate Supabase JWT if present.
    Returns None if no token provided (for guest users).
    Returns AuthUser if valid token provided.
    Raises 401 only if token is present but invalid.
    """
    if token is None:
        return None

    try:
        header = jwt.get_unverified_header(token.credentials)
        alg = header.get("alg", "HS256")

        if alg.startswith("HS"):
            key = settings.SUPABASE_JWT_SECRET
            algorithms = ["HS256"]
        else:
            kid = header.get("kid")
            jwk_key = await _get_jwk_for_kid(kid) if kid else None
            if not jwk_key:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Could not validate credentials",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            key = jwk_key
            algorithms = [alg]

        try:
            payload = jwt.decode(
                token.credentials,
                key,
                algorithms=algorithms,
                audience="authenticated",
                options={"verify_aud": True},
            )
        except JWTError as e:
            if "audience" in str(e).lower() or "aud" in str(e).lower():
                payload = jwt.decode(
                    token.credentials,
                    key,
                    algorithms=algorithms,
                    options={"verify_aud": False},
                )
            else:
                raise

        return AuthUser(**payload)

    except (JWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
