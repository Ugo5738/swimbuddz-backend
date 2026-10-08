"""Pure, auditable Pool Access pricing and redemption policy."""
import hashlib
import hmac
import os
import uuid
from fastapi import HTTPException


def quoted_amount(selling_price_kobo: int, quantity: int) -> int:
    if selling_price_kobo <= 0 or not 1 <= quantity <= 25:
        raise ValueError("Invalid price or admission quantity")
    return selling_price_kobo * quantity


def reconciled_cost(negotiated_cost_kobo: int, basis: str, verified_count: int) -> int:
    """Pay per redeemed swimmer, or one group charge if any swimmer entered."""
    if negotiated_cost_kobo < 0 or verified_count < 0:
        raise ValueError("Negative inputs are not allowed")
    if basis == "per_person":
        return negotiated_cost_kobo * verified_count
    if basis == "per_group":
        return negotiated_cost_kobo if verified_count else 0
    raise ValueError("Unsupported cost basis")


def ticket_for(admission_id: uuid.UUID) -> str:
    """Reproducible QR credential; no bearer secret persisted in the database."""
    key = os.environ.get("POOL_ACCESS_QR_SECRET")
    if not key or len(key) < 32:
        raise HTTPException(503, "Pool Access QR is not configured")
    text = f"pa1.{admission_id}"
    signature = hmac.new(key.encode(), text.encode(), hashlib.sha256).hexdigest()
    return f"{text}.{signature}"


def admission_id_from_ticket(ticket: str) -> uuid.UUID:
    try:
        version, raw_id, signature = ticket.split(".")
        if version != "pa1":
            raise ValueError("Unsupported ticket")
        admission_id = uuid.UUID(raw_id)
        canonical = ticket_for(admission_id)
        if not hmac.compare_digest(canonical, f"{version}.{raw_id}.{signature}"):
            raise ValueError("Invalid ticket signature")
        return admission_id
    except (ValueError, AttributeError) as exc:
        raise HTTPException(400, "Invalid admission QR") from exc
