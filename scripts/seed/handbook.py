"""Seed the initial handbook version (v1.0) into the database.

Reads the full handbook from docs/academy/COACH_HANDBOOK.md and
stores it as the current handbook version.
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
from services.members_service.models import HandbookVersion
from sqlalchemy.future import select

# Project root (swimbuddz/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _load_handbook() -> str:
    """Load the handbook content from docs/academy/COACH_HANDBOOK.md."""
    handbook_path = PROJECT_ROOT / "docs" / "academy" / "COACH_HANDBOOK.md"
    if not handbook_path.exists():
        raise FileNotFoundError(
            f"Handbook file not found: {handbook_path}. "
            "Ensure docs/academy/COACH_HANDBOOK.md exists."
        )
    return handbook_path.read_text(encoding="utf-8")


async def seed_handbook():
    """Seed the initial v1.0 handbook version."""
    async with AsyncSessionLocal() as session:
        # Check if version 1.0 already exists
        result = await session.execute(
            select(HandbookVersion).where(HandbookVersion.version == "1.0")
        )
        existing = result.scalar_one_or_none()
        if existing:
            print("  Handbook version 1.0 already exists, skipping")
            return

        content = _load_handbook()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        handbook = HandbookVersion(
            id=uuid4(),
            version="1.0",
            title="SwimBuddz Coach Handbook",
            content=content,
            content_hash=content_hash,
            effective_date=date(2026, 2, 6),
            is_current=True,
            created_by_id=None,  # System-seeded
        )
        session.add(handbook)
        await session.commit()
        print("  Seeded handbook version 1.0")


if __name__ == "__main__":
    asyncio.run(seed_handbook())
