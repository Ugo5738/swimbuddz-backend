"""Seed the Chat Safeguarding Policy as an AgreementVersion.

Reads ``scripts/seed-data/policy/chat_safeguarding_v1.0.md`` and inserts it as a
row in ``agreement_versions`` with ``agreement_type="safeguarding"``, ``version="1.0"``.

Idempotent: a second run with the same content leaves the DB untouched; if the
file content changes, the seed will refuse to overwrite a different hash (you
must create a new version instead).
"""

import asyncio
import hashlib
import os
import sys
from datetime import date
from pathlib import Path
from uuid import uuid4

# Add backend root to path so we can import modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from sqlalchemy import and_
from sqlalchemy.future import select

from libs.db.config import AsyncSessionLocal
from services.members_service.models import AgreementType, AgreementVersion

# Project root (swimbuddz-backend/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

POLICY_FILE = (
    PROJECT_ROOT / "scripts" / "seed-data" / "policy" / "chat_safeguarding_v1.0.md"
)
POLICY_VERSION = "1.0"
POLICY_TITLE = "SwimBuddz Chat Safeguarding Policy"
# Effective date is the date the policy takes effect once accepted in production.
# Kept conservative — update when the policy is approved and launched.
POLICY_EFFECTIVE_DATE = date(2026, 5, 1)


def _load_policy() -> str:
    if not POLICY_FILE.exists():
        raise FileNotFoundError(
            f"Chat safeguarding policy file not found: {POLICY_FILE}. "
            "Ensure the file is present in scripts/seed-data/policy/."
        )
    return POLICY_FILE.read_text(encoding="utf-8")


async def seed_chat_safeguarding():
    """Seed chat safeguarding policy v1.0 if not present.

    Skips if an identical (agreement_type, version, content_hash) row already exists.
    Raises if a (agreement_type, version) row exists with a *different* content hash —
    in that case, bump the version number rather than silently overwriting.
    """
    content = _load_policy()
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgreementVersion).where(
                and_(
                    AgreementVersion.agreement_type == AgreementType.SAFEGUARDING.value,
                    AgreementVersion.version == POLICY_VERSION,
                )
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            if existing.content_hash == content_hash:
                print(
                    f"  Chat safeguarding v{POLICY_VERSION} already present "
                    f"(content_hash matches). Skipping."
                )
                return
            raise RuntimeError(
                f"agreement_versions has chat safeguarding v{POLICY_VERSION} "
                f"with a different content_hash. Refusing to overwrite. "
                f"Create a new version (v1.1+) instead of editing v1.0."
            )

        row = AgreementVersion(
            id=uuid4(),
            agreement_type=AgreementType.SAFEGUARDING.value,
            version=POLICY_VERSION,
            title=POLICY_TITLE,
            content=content,
            content_hash=content_hash,
            effective_date=POLICY_EFFECTIVE_DATE,
            is_current=True,
            created_by_id=None,
        )
        session.add(row)
        await session.commit()
        print(
            f"  Seeded chat safeguarding policy v{POLICY_VERSION} (current), "
            f"hash={content_hash[:12]}..."
        )


if __name__ == "__main__":
    asyncio.run(seed_chat_safeguarding())
