#!/usr/bin/env python3
"""
Seed discount codes for returning members.

Creates two promotional discounts:
1. LEGACY2025 - 75% off Community membership for 50 returning members
2. LEGACY2025CLUB - 100% off Club membership for 15 returning members

Both valid for 20 days from the script execution date.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

# Add project root to path
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, project_root)

from libs.db.config import AsyncSessionLocal
from services.payments_service.models import Discount, DiscountType
from sqlalchemy.future import select


async def seed_discounts():
    """Create promotional discount codes for returning members."""
    # Calculate validity dates (20 days from now)
    now = datetime.now(timezone.utc)
    valid_from = now
    valid_until = now + timedelta(days=20)

    discounts = [
        {
            "code": "LEGACY2025",
            "description": "Welcome back! 75% off Community membership for returning members who were with us before the website launch.",
            "discount_type": DiscountType.PERCENTAGE,
            "value": 75.0,
            "applies_to": ["COMMUNITY"],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "max_uses": 50,
            "current_uses": 0,
            "max_uses_per_user": 1,
            "is_active": True,
        },
        {
            "code": "LEGACY2025CLUB",
            "description": "Welcome back! 100% off Club membership for our loyal returning members who were with us before the website launch.",
            "discount_type": DiscountType.PERCENTAGE,
            "value": 100.0,
            "applies_to": ["CLUB"],
            "valid_from": valid_from,
            "valid_until": valid_until,
            "max_uses": 15,
            "current_uses": 0,
            "max_uses_per_user": 1,
            "is_active": True,
        },
    ]

    async with AsyncSessionLocal() as session:
        async with session.begin():
            for discount_data in discounts:
                # Check if discount already exists
                stmt = select(Discount).where(Discount.code == discount_data["code"])
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    print(
                        f"  Discount '{discount_data['code']}' already exists, skipping..."
                    )
                    continue

                discount = Discount(**discount_data)
                session.add(discount)
                print(f"  Created discount: {discount_data['code']}")
                print(f"    - Type: {discount_data['discount_type'].value}")
                print(f"    - Value: {discount_data['value']}%")
                print(f"    - Applies to: {discount_data['applies_to']}")
                print(f"    - Max uses: {discount_data['max_uses']}")
                print(f"    - Valid until: {valid_until.strftime('%Y-%m-%d')}")

            print("\n✓ Discount codes seeded successfully!")


if __name__ == "__main__":
    print("Seeding discount codes for returning members...")
    asyncio.run(seed_discounts())
