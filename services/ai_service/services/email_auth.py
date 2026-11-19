"""Email ownership proof for paid public StrokeLab credits.

A guest can redeem a purchased license key, but *spending* already-granted
credits requires demonstrating control of the credit account's mailbox.
Short-lived codes and bearer sessions are held in Redis; no DB migration.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager

from arq import create_pool
from fastapi import HTTPException

from libs.common.arq_config import get_redis_settings
from libs.common.emails.client import get_email_client
from services.ai_service.services.credit_ops import canonicalize_email

_CODE_TTL = 600
_SESSION_TTL = 86400
_EMAIL_COOLDOWN = 60
_MAX_ATTEMPTS = 5


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _email_key(email: str, suffix: str) -> str:
    return f"strokelab:email-auth:{_digest(canonicalize_email(email))}:{suffix}"


@asynccontextmanager
async def _redis():
    try:
        pool = await create_pool(get_redis_settings())
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Email verification temporarily unavailable") from exc
    try:
        yield pool
    finally:
        await pool.close()


async def request_email_code(email: str, *, requester: str = "") -> None:
    """Send a short-lived email code with per-email and per-source limits.

    Do not reveal whether an email already owns credits.
    """
    code = f"{secrets.randbelow(1_000_000):06d}"
    digest = _digest(code)
    async with _redis() as redis:
        cooldown = _email_key(email, "cooldown")
        ip_key = f"strokelab:email-auth:ip:{_digest(requester or 'unknown')}"
        count = await redis.incr(ip_key)
        if count == 1:
            await redis.expire(ip_key, 3600)
        if count > 12:
            raise HTTPException(status_code=429, detail="Too many verification requests")
        claimed = await redis.set(cooldown, "1", ex=_EMAIL_COOLDOWN, nx=True)
        if not claimed:
            raise HTTPException(status_code=429, detail="Please wait before requesting another code")
        await redis.set(_email_key(email, "code"), digest, ex=_CODE_TTL)
        await redis.delete(_email_key(email, "attempts"))

    success = await get_email_client().send(
        to_email=email,
        subject="Your SwimBuddz StrokeLab verification code",
        body=f"Your StrokeLab verification code is {code}. It expires in 10 minutes. If you didn't request it, ignore this message.",
    )
    if not success:
        async with _redis() as redis:
            await redis.delete(_email_key(email, "code"), _email_key(email, "cooldown"))
        raise HTTPException(status_code=503, detail="Unable to send verification email")


async def verify_email_code(email: str, code: str) -> str:
    if len(code) != 6 or not code.isascii() or not code.isdigit():
        raise HTTPException(status_code=400, detail="Invalid verification code")
    async with _redis() as redis:
        attempts_key = _email_key(email, "attempts")
        attempts = await redis.incr(attempts_key)
        if attempts == 1:
            await redis.expire(attempts_key, _CODE_TTL)
        if attempts > _MAX_ATTEMPTS:
            raise HTTPException(status_code=429, detail="Too many attempts. Request another code")
        expected = await redis.get(_email_key(email, "code"))
        if not expected or not hmac.compare_digest(
            expected.decode() if isinstance(expected, bytes) else str(expected), _digest(code)
        ):
            raise HTTPException(status_code=400, detail="Invalid or expired verification code")
        await redis.delete(_email_key(email, "code"), attempts_key)
        token = secrets.token_urlsafe(32)
        await redis.set(f"strokelab:email-auth:session:{_digest(token)}", canonicalize_email(email), ex=_SESSION_TTL)
        return token


async def require_paid_credit_owner(email: str, token: str | None) -> None:
    if not token:
        raise HTTPException(status_code=403, detail={"reason": "email_verification_required"})
    async with _redis() as redis:
        actual = await redis.get(f"strokelab:email-auth:session:{_digest(token)}")
    canonical = canonicalize_email(email)
    actual_email = actual.decode() if isinstance(actual, bytes) else str(actual or "")
    if not hmac.compare_digest(actual_email, canonical):
        raise HTTPException(status_code=403, detail={"reason": "email_verification_required"})
