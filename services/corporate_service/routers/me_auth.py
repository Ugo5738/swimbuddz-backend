"""HR-portal magic-link authentication routes (``/corporate/me/auth/*``).

Two endpoints. Both are public — the magic-link request endpoint must be
callable by anyone with an email, and the verify endpoint by anyone holding
a magic-link token. Rate-limited per IP to keep the email queue from
being weaponised.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.rate_limit import limiter
from libs.db.session import get_async_db
from services.corporate_service.auth import (
    MAGIC_LINK_TTL,
    PURPOSE_MAGIC,
    PURPOSE_SESSION,
    SESSION_TTL,
    mint_token,
    verify_token,
)
from services.corporate_service.models import (
    CorporateContact,
    CorporateTouchpoint,
    TouchpointDirection,
    TouchpointType,
)
from services.corporate_service.schemas import (
    RequestMagicLinkRequest,
    RequestMagicLinkResponse,
    VerifyMagicLinkRequest,
    VerifyMagicLinkResponse,
)

logger = get_logger(__name__)
router = APIRouter(tags=["corporate-me-auth"])


def _build_magic_link_email(
    *, contact_name: str, company_name: str, link: str
) -> tuple[str, str]:
    """Return ``(plain, html)`` for the magic-link email."""
    first_name = (contact_name or "there").split()[0]
    plain = (
        f"Hi {first_name},\n\n"
        f"You requested a link to the {company_name} SwimBuddz wellness "
        "portal. Click below to sign in (valid for 24 hours):\n\n"
        f"{link}\n\n"
        "If you didn't request this, you can ignore the email — no one "
        "has gained access to your account.\n\n"
        "— SwimBuddz"
    )
    html = (
        f"<p>Hi {first_name},</p>"
        f"<p>You requested a link to the <strong>{company_name}</strong> "
        "SwimBuddz wellness portal. Click below to sign in "
        "(valid for 24 hours):</p>"
        f'<p><a href="{link}">Open the portal</a></p>'
        "<p>If you didn't request this, you can ignore the email — no "
        "one has gained access to your account.</p>"
        "<p>— SwimBuddz</p>"
    )
    return plain, html


@router.post(
    "/me/auth/request-link",
    response_model=RequestMagicLinkResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("3/minute")
async def request_magic_link(
    request: Request,
    payload: RequestMagicLinkRequest,
    db: AsyncSession = Depends(get_async_db),
) -> RequestMagicLinkResponse:
    """Send a magic link to a corporate contact's email.

    Always returns ``sent=true``. If the email doesn't match an active
    contact, we still return success but skip the send — this stops naive
    email-enumeration attacks. Rate limit keeps a single IP from spamming
    requests for many emails.
    """
    email_norm = payload.email.lower()

    contact = (
        await db.execute(
            select(CorporateContact).where(
                func.lower(CorporateContact.primary_contact_email) == email_norm,
                CorporateContact.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()

    if contact is None:
        # Silent no-op for unknown emails. Log it so admins can audit the
        # enumeration attempts but don't tell the client.
        logger.info("corporate.me magic-link requested for unknown email")
        return RequestMagicLinkResponse(sent=True)

    token = mint_token(
        contact_id=contact.id,
        purpose=PURPOSE_MAGIC,
        ttl=MAGIC_LINK_TTL,
        company_name=contact.company_name,
    )

    sep = "&" if "?" in payload.callback_url else "?"
    link = f"{payload.callback_url}{sep}token={token}"

    plain, html = _build_magic_link_email(
        contact_name=contact.primary_contact_name,
        company_name=contact.company_name,
        link=link,
    )

    delivered = False
    try:
        email_client = get_email_client()
        delivered = await email_client.send(
            to_email=contact.primary_contact_email,
            subject="Your SwimBuddz portal sign-in link",
            body=plain,
            html_body=html,
        )
    except Exception:
        logger.warning(
            "Failed to send magic-link email to %s (best-effort, returning ok)",
            contact.primary_contact_email,
            exc_info=True,
        )

    # Log a touchpoint so admins can see HR users are actually using
    # (or trying to use) the portal. Helpful for diagnosing "I never
    # got the email" complaints.
    summary = f"Magic-link email {'sent' if delivered else 'attempted (send failed)'}"
    db.add(
        CorporateTouchpoint(
            contact_id=contact.id,
            type=TouchpointType.NOTE,
            direction=TouchpointDirection.OUTBOUND,
            occurred_at=utc_now(),
            summary=summary,
            outcome="HR portal sign-in flow",
        )
    )
    await db.commit()

    return RequestMagicLinkResponse(sent=True)


@router.post(
    "/me/auth/verify",
    response_model=VerifyMagicLinkResponse,
)
async def verify_magic_link(
    payload: VerifyMagicLinkRequest,
    db: AsyncSession = Depends(get_async_db),
) -> VerifyMagicLinkResponse:
    """Swap a magic-link token for a session token.

    Re-asserts the contact is still active so admins can revoke access by
    soft-deleting the contact even after a magic link was emailed.
    """
    claims = verify_token(payload.token, expected_purpose=PURPOSE_MAGIC)
    contact = (
        await db.execute(
            select(CorporateContact).where(
                CorporateContact.id == claims.contact_id,
                CorporateContact.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if not contact:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Corporate contact not found or deactivated",
        )

    session_token = mint_token(
        contact_id=contact.id,
        purpose=PURPOSE_SESSION,
        ttl=SESSION_TTL,
        company_name=contact.company_name,
    )
    expires_at = datetime.now(timezone.utc) + SESSION_TTL

    return VerifyMagicLinkResponse(
        session_token=session_token,
        expires_at=expires_at,
        contact_id=contact.id,
        company_name=contact.company_name,
        primary_contact_name=contact.primary_contact_name,
    )
