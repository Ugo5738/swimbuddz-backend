#!/usr/bin/env python3
"""Quick local preview of the Wrapped card without Docker.

Usage:
    python scripts/preview_card.py              # generates square card
    python scripts/preview_card.py story        # generates story card
    python scripts/preview_card.py square 42    # square card with 42 pool hours

Output: Opens the generated PNG in your default image viewer.
"""

import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


class MockReport:
    """Mock report data for previewing the card design."""

    member_name = "Ugochukwu Nwachukwu"
    member_auth_id = "mock-auth-id"
    year = 2026
    quarter = 1
    pool_hours = 2.0
    total_sessions_attended = 6
    attendance_rate = 1.0
    streak_longest = 1
    streak_current = 1
    is_first_quarter = True
    attendance_percentile = 0.85  # top 15%
    milestones_achieved = 0
    volunteer_hours = 0
    bubbles_earned = 13
    events_attended = 0
    rides_taken = 0
    certificates_earned = 0
    orders_placed = 0
    card_image_path = None


async def main():
    fmt = sys.argv[1] if len(sys.argv) > 1 else "square"
    report = MockReport()

    if len(sys.argv) > 2:
        report.pool_hours = float(sys.argv[2])

    # Monkey-patch the photo fetcher to skip HTTP call in local preview
    import services.reporting_service.services.card_generator as cg

    async def _no_photo(*a, **kw):
        return None

    cg._fetch_member_photo_url = _no_photo

    from services.reporting_service.services.card_generator import generate_card_image

    data = await generate_card_image(report, fmt)

    out = Path(f"/tmp/swimbuddz_card_preview_{fmt}.png")
    out.write_bytes(data)
    print(f"Card saved to {out} ({len(data)} bytes)")

    # Open in default viewer
    import subprocess

    subprocess.run(["open", str(out)])


if __name__ == "__main__":
    asyncio.run(main())
