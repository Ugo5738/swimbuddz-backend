"""Seed agreement versions into the database.

Seeds:
- v1.0: Full coach_agreement_v1.0.md content (current version)
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


def _load_full_agreement() -> str:
    """Load the full agreement content from scripts/seed-data/academy/coach_agreement_v1.0.md."""
    agreement_path = (
        PROJECT_ROOT / "scripts" / "seed-data" / "academy" / "coach_agreement_v1.0.md"
    )
    if not agreement_path.exists():
        raise FileNotFoundError(
            f"Full agreement file not found: {agreement_path}. "
            "Ensure scripts/seed-data/academy/coach_agreement_v1.0.md exists."
        )
    return agreement_path.read_text(encoding="utf-8")


async def seed_agreement():
    """Seed agreement version v1.0 from COACH_AGREEMENT.md."""
    async with AsyncSessionLocal() as session:
        # Check if the coach-agreement v1.0 already exists. Scope by
        # agreement_type — other policy types (e.g. safeguarding) may also
        # use version strings like "1.0", so we must not match across types.
        result = await session.execute(
            select(AgreementVersion).where(
                and_(
                    AgreementVersion.agreement_type
                    == AgreementType.COACH_AGREEMENT.value,
                    AgreementVersion.version == "1.0",
                )
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            print("  Coach agreement version 1.0 already exists, skipping")
            return

        content = _load_full_agreement()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        v1 = AgreementVersion(
            id=uuid4(),
            agreement_type=AgreementType.COACH_AGREEMENT.value,
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
        print("  Seeded coach agreement version 1.0 (current)")


if __name__ == "__main__":
    asyncio.run(seed_agreement())
