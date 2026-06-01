"""Client for posting journal entries to the Ledger Service.

Lives alongside ``libs.common.service_client`` (not inside it) because it has a
deliberately different failure contract.

⚠️ IMPORTANT — this client RAISES on failure. It is NOT best-effort.

Contrast ``service_client.wallet.emit_rewards_event``, which swallows every
exception and returns ``None`` so a rewards miss never blocks the caller. A
*journal entry* is different: a silently-dropped entry is a books error that
corrupts the trial balance. So ``post_journal_entry`` propagates errors, and the
caller is responsible for catching and writing a dead-letter row (see
implementation plan §5 / task P1.8) so the entry can be replayed. Never wrap
this in a bare ``except: pass``.

The full posting endpoint lands in PR-2 (task P1.6); this module is the stable
client contract (tasks P0.7 / P1.10).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional, TypedDict, Union

from libs.common.config import get_settings

from .service_client.core import internal_post


class JournalLineSpec(TypedDict, total=False):
    """One line of a journal entry. Exactly one of debit/credit is non-zero.

    Amounts are integer minor units (kobo). Accounts are referenced by their
    stable ``account_ref`` (the CoA ``maps_to`` value), never by code.
    """

    account_ref: str  # required — e.g. "paystack_clearing"
    debit: int  # minor units (kobo); 0 or omitted if this is a credit line
    credit: int  # minor units (kobo); 0 or omitted if this is a debit line
    currency: str  # ISO 4217, e.g. "NGN" (defaults to org base currency server-side)
    cost_center: Optional[str]  # e.g. "lagos_yaba"
    dimension_1: Optional[str]  # e.g. domain: "academy"
    dimension_2: Optional[str]  # e.g. program: "cohort_12"
    member_ref: Optional[str]  # customer-level ref for AR / per-member reports
    external_ref: Optional[str]  # opaque pointer to the operational row
    tax_code_ref: Optional[str]  # e.g. "NG_VAT_STANDARD" (PR-6+)
    description: Optional[str]


def _to_iso_date(value: Union[str, date, datetime]) -> str:
    """Normalise an entry date to an ISO date string (YYYY-MM-DD)."""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.date().isoformat()
    return value.isoformat()


async def post_journal_entry(
    *,
    entry_date: Union[str, date, datetime],
    description: str,
    source_service: str,
    source_type: str,
    source_id: str,
    lines: list[JournalLineSpec],
    calling_service: str,
    org_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    """Post a balanced double-entry journal entry to the ledger.

    Idempotency key is derived deterministically as
    ``f"{source_service}:{source_type}:{source_id}"`` so any replay from any
    code path collapses to a single entry.

    Args:
        entry_date: Accounting date of the entry (str ISO, date, or datetime).
        description: Human-readable description.
        source_service: Emitting service, e.g. "payments".
        source_type: Business event, e.g. "charge_success".
        source_id: ID of the source row, e.g. the Payment reference.
        lines: Journal lines; ``sum(debit) == sum(credit)`` (validated server-side).
        calling_service: Name of the calling service (JWT "sub" claim).
        org_id: Target organization UUID. If omitted, the ledger resolves it to
            ``LEDGER_DEFAULT_ORG_ID`` (Phase 1, single-tenant SwimBuddz).
        metadata: Optional free-form context stored on the entry.

    Returns:
        The ledger's ``JournalEntryResult`` dict: ``{entry_id, status, period_id}``.

    Raises:
        httpx.HTTPStatusError / httpx.RequestError on any failure. The CALLER
        must catch and dead-letter (do NOT swallow). See plan §5 / P1.8.
    """
    settings = get_settings()
    idempotency_key = f"{source_service}:{source_type}:{source_id}"
    payload: dict[str, Any] = {
        "idempotency_key": idempotency_key,
        "entry_date": _to_iso_date(entry_date),
        "description": description,
        "source_service": source_service,
        "source_type": source_type,
        "source_id": source_id,
        "lines": lines,
        "metadata": metadata or {},
    }
    if org_id is not None:
        payload["org_id"] = org_id

    resp = await internal_post(
        service_url=settings.LEDGER_SERVICE_URL,
        path="/internal/ledger/journal-entries",
        calling_service=calling_service,
        json=payload,
    )
    resp.raise_for_status()  # RAISES on failure — caller dead-letters. Not best-effort.
    return resp.json()


__all__ = ["post_journal_entry", "JournalLineSpec"]
