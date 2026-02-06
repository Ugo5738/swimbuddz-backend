"""Seed agreement versions into the database.

Seeds:
- v1.0: Full COACH_AGREEMENT.md content (current version)
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

from libs.db.config import AsyncSessionLocal
from services.members_service.models import AgreementVersion
from sqlalchemy.future import select

# Project root (swimbuddz-backend/)
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _load_full_agreement() -> str:
    """Load the full agreement content from docs/academy/COACH_AGREEMENT.md."""
    agreement_path = PROJECT_ROOT / "docs" / "academy" / "COACH_AGREEMENT.md"
    if not agreement_path.exists():
        raise FileNotFoundError(
            f"Full agreement file not found: {agreement_path}. "
            "Ensure docs/academy/COACH_AGREEMENT.md exists."
        )
    return agreement_path.read_text(encoding="utf-8")


async def seed_agreement():
    """Seed agreement version v1.0 from COACH_AGREEMENT.md."""
    async with AsyncSessionLocal() as session:
        # Check if v1.0 already exists
        result = await session.execute(
            select(AgreementVersion).where(AgreementVersion.version == "1.0")
        )
        existing = result.scalar_one_or_none()
        if existing:
            print("  Agreement version 1.0 already exists, skipping")
            return

        content = _load_full_agreement()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        v1 = AgreementVersion(
            id=uuid4(),
            version="1.0",
            title="SwimBuddz Coach Agreement",
            content=content,
            content_hash=content_hash,
            effective_date=date(2026, 2, 1),
            is_current=True,
            created_by_id=None,
        )
        session.add(v1)
        await session.commit()
        print("  Seeded agreement version 1.0 (current)")


if __name__ == "__main__":
    asyncio.run(seed_agreement())
