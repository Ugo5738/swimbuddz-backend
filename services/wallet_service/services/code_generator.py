"""Referral code generator — SB-{NAME}-{XXXX} format."""

import random
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.wallet_service.models.referral import ReferralCode

# Exclude ambiguous characters: 0/O, 1/I/L
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_SUFFIX_LENGTH = 4
_MAX_NAME_LENGTH = 8
_MAX_RETRIES = 5


def _clean_name(first_name: str) -> str:
    """Uppercase, strip non-alpha, truncate."""
    cleaned = re.sub(r"[^A-Za-z]", "", first_name).upper()
    return cleaned[:_MAX_NAME_LENGTH] or "MEMBER"


def _random_suffix() -> str:
    return "".join(random.choices(_ALPHABET, k=_SUFFIX_LENGTH))


async def generate_referral_code(first_name: str, db: AsyncSession) -> str:
    """Generate a unique referral code in SB-{NAME}-{XXXX} format.

    Checks for collisions up to MAX_RETRIES times.
    """
    name_part = _clean_name(first_name)

    for _ in range(_MAX_RETRIES):
        code = f"SB-{name_part}-{_random_suffix()}"
        result = await db.execute(
            select(ReferralCode.id).where(ReferralCode.code == code)
        )
        if result.scalar_one_or_none() is None:
            return code

    raise RuntimeError(
        f"Failed to generate unique referral code after {_MAX_RETRIES} attempts"
    )
