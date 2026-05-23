"""HR-portal authentication for corporate_service.

CorporateContact records aren't Supabase users — HR contacts don't sign up
for SwimBuddz, they're added by the admin running the sales pipeline. So we
issue our own short-lived JWTs to grant them read-only access to their
company's program data.

Flow:
    1. HR enters their email at /corporate-portal.
    2. POST /corporate/me/auth/request-link
       — look up CorporateContact by email
       — issue a MAGIC_LINK token (24h) signed with SUPABASE_JWT_SECRET
       — email a URL containing the token
    3. HR clicks the link → frontend extracts ?token=…, posts to
       POST /corporate/me/auth/verify
       — backend validates the magic-link token, swaps it for a longer-lived
         SESSION token (7d) which the frontend stores and sends on /me/*
         requests via Authorization: Bearer ...
    4. require_corporate_admin decodes the session token and returns the
       corresponding CorporateContact.

We deliberately don't reuse Supabase auth — corporate contacts shouldn't
appear in the members table, and conflating the two means tangling RLS
and member dashboards with B2B users. Their world is read-only and
scoped to a single company.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Annotated, Literal, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.db.session import get_async_db
from services.corporate_service.models import CorporateContact

# Token TTLs. Magic links must be short (clicked or thrown out). Session
# tokens are long enough not to annoy returning visitors but short enough
# that a stolen one becomes useless quickly.
MAGIC_LINK_TTL = timedelta(hours=24)
SESSION_TTL = timedelta(days=7)

# Claim values for the ``purpose`` field, so a magic-link token can't be
# used as a session token and vice-versa.
PURPOSE_MAGIC = "corp_magic_link"
PURPOSE_SESSION = "corp_session"

TokenPurpose = Literal["corp_magic_link", "corp_session"]

_security = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Token mint / verify helpers
# ---------------------------------------------------------------------------


def _settings():
    # Function indirection so tests can mock get_settings() lazily.
    return get_settings()


def mint_token(
    *,
    contact_id: uuid.UUID,
    purpose: TokenPurpose,
    ttl: timedelta,
    company_name: Optional[str] = None,
) -> str:
    """Sign a JWT for a corporate contact.

    The token uses the project's SUPABASE_JWT_SECRET so it stays compatible
    with our existing JWT tooling, but it's clearly distinguished from
    member/admin tokens by ``sub`` prefix and ``purpose`` claim.
    """
    settings = _settings()
    now_dt = utc_now()
    now = int(now_dt.timestamp())
    exp = int((now_dt + ttl).timestamp())
    payload: dict = {
        "sub": f"corporate_contact:{contact_id}",
        "contact_id": str(contact_id),
        "purpose": purpose,
        "iat": now,
        "exp": exp,
    }
    if company_name is not None:
        payload["company_name"] = company_name
    return jwt.encode(payload, settings.SUPABASE_JWT_SECRET, algorithm="HS256")


class CorporateTokenClaims(BaseModel):
    """Validated claims off a corporate-portal JWT."""

    contact_id: uuid.UUID
    purpose: TokenPurpose
    company_name: Optional[str] = None


def verify_token(token: str, *, expected_purpose: TokenPurpose) -> CorporateTokenClaims:
    """Decode + validate a portal token.

    Raises HTTPException(401) if anything's off — expired, wrong purpose,
    signature mismatch, malformed payload.
    """
    settings = _settings()
    try:
        payload = jwt.decode(
            token,
            settings.SUPABASE_JWT_SECRET,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid corporate-portal token: {exc}",
        ) from exc

    if payload.get("purpose") != expected_purpose:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                f"Token purpose mismatch (expected {expected_purpose!r}, "
                f"got {payload.get('purpose')!r})"
            ),
        )
    try:
        return CorporateTokenClaims(
            contact_id=uuid.UUID(payload["contact_id"]),
            purpose=payload["purpose"],
            company_name=payload.get("company_name"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Malformed corporate-portal token: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def require_corporate_admin(
    creds: Annotated[
        Optional[HTTPAuthorizationCredentials], Depends(_security)
    ] = None,
    db: AsyncSession = Depends(get_async_db),
) -> CorporateContact:
    """Resolve the calling HR contact from their bearer token.

    Returns the CorporateContact row — endpoints query off this for
    company-scoped data, so they never need to trust a path parameter to
    avoid leaking another company's programs.
    """
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )
    claims = verify_token(creds.credentials, expected_purpose=PURPOSE_SESSION)
    contact = (
        await db.execute(
            select(CorporateContact).where(
                CorporateContact.id == claims.contact_id,
                CorporateContact.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Corporate contact not found or deactivated",
        )
    return contact
