"""Currency conversion utilities for SwimBuddz.

Internal storage unit: kobo (smallest NGN unit, 100 kobo = ₦1).
Wallet unit: Bubbles (₦100 = 1 Bubble = 10,000 kobo).
API / display unit: Naira (float, e.g. 1500.0 = ₦1,500).

Conversion chain
----------------
Naira × 100 → Kobo
Naira ÷ 100 → Bubbles
Kobo  ÷ 100 → Naira
Kobo  ÷ 10,000 → Bubbles
"""

from __future__ import annotations

# ─── constants ───────────────────────────────────────────────────────────────

KOBO_PER_NAIRA: int = 100
NAIRA_PER_BUBBLE: int = 100
KOBO_PER_BUBBLE: int = KOBO_PER_NAIRA * NAIRA_PER_BUBBLE  # 10,000


# ─── conversion helpers ───────────────────────────────────────────────────────


def naira_to_kobo(naira: float) -> int:
    """Convert Naira to kobo (round half-up). ₦1 = 100 kobo."""
    return round(naira * KOBO_PER_NAIRA)


def kobo_to_naira(kobo: int) -> float:
    """Convert kobo to Naira. 100 kobo = ₦1."""
    return kobo / KOBO_PER_NAIRA


def kobo_to_bubbles(kobo: int) -> int:
    """Convert kobo to Bubbles (floor division). 10,000 kobo = 1 Bubble."""
    return kobo // KOBO_PER_BUBBLE


def bubbles_to_kobo(bubbles: int) -> int:
    """Convert Bubbles to kobo. 1 Bubble = 10,000 kobo."""
    return bubbles * KOBO_PER_BUBBLE


def naira_to_bubbles(naira: float) -> int:
    """Convert Naira to Bubbles. ₦100 = 1 Bubble."""
    return kobo_to_bubbles(naira_to_kobo(naira))


def bubbles_to_naira(bubbles: int) -> float:
    """Convert Bubbles to Naira. 1 Bubble = ₦100."""
    return bubbles * NAIRA_PER_BUBBLE
