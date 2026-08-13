"""Canonical guest identity values shared by booking and guest-pass flows."""

import re


def normalize_guest_phone(phone: str) -> str:
    """Return a stable Nigerian/E.164-like value for guest deduplication."""
    stripped = phone.strip()
    digits = re.sub(r"\D", "", stripped)
    if stripped.startswith("+"):
        return f"+{digits}"
    if digits.startswith("234"):
        return f"+{digits}"
    if len(digits) == 11 and digits.startswith("0"):
        return f"+234{digits[1:]}"
    return digits
