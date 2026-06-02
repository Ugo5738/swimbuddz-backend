"""The atomic double-entry posting engine.

`post_entry` is the single path by which journal entries are created. It is
idempotent (by (org_id, idempotency_key)), validates the entry balances and
that its period is open, resolves account refs to ids, writes the entry + lines,
recomputes affected balances, and audit-logs the post — all within the caller's
transaction (the route commits). Entries are immutable; corrections are
reversing entries (PR-3).
"""

from __future__ import annotations

import uuid
from typing import Optional

from libs.common.datetime_utils import utc_now
from services.ledger_service.models import (
    AuditLog,
    CostCenter,
    JournalEntry,
    JournalLine,
    Organization,
)
from services.ledger_service.models.enums import (
    AuditActionType,
    EntryStatus,
    PeriodStatus,
)
from services.ledger_service.schemas.journal import (
    JournalEntryCreate,
    JournalEntryResult,
)
from services.ledger_service.services.accounts import resolve_account_ids
from services.ledger_service.services.balances import recompute_account_balances
from services.ledger_service.services.periods import resolve_or_create_period
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class LedgerError(Exception):
    """Base for posting errors the route maps to 4xx."""


class UnbalancedEntryError(LedgerError):
    """sum(debits) != sum(credits)."""


class UnresolvedAccountError(LedgerError):
    """An account_ref doesn't resolve to an active account in the org."""


class PeriodClosedError(LedgerError):
    """The entry's period is not open."""


class EntryNotFoundError(LedgerError):
    """The entry to reverse doesn't exist in this org."""


class AlreadyReversedError(LedgerError):
    """The entry has already been reversed."""


async def _get_by_idempotency(
    session: AsyncSession, org_id: uuid.UUID, key: str
) -> Optional[JournalEntry]:
    return (
        await session.execute(
            select(JournalEntry).where(
                JournalEntry.org_id == org_id,
                JournalEntry.idempotency_key == key,
            )
        )
    ).scalar_one_or_none()


def _result(entry: JournalEntry, *, replay: bool) -> JournalEntryResult:
    return JournalEntryResult(
        entry_id=entry.id,
        status=entry.status.value,
        period_id=entry.period_id,
        idempotent_replay=replay,
    )


async def post_entry(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    payload: JournalEntryCreate,
    posted_by_user_id: Optional[uuid.UUID] = None,
    posted_by_service: Optional[str] = None,
) -> JournalEntryResult:
    """Post a balanced journal entry. Idempotent; runs in the caller's transaction."""
    # 1. Idempotency — replays return the original, no new entry.
    existing = await _get_by_idempotency(session, org_id, payload.idempotency_key)
    if existing is not None:
        return _result(existing, replay=True)

    # 2. Balance (schema validated already; re-assert defensively).
    total_debit = sum(line.debit for line in payload.lines)
    total_credit = sum(line.credit for line in payload.lines)
    if total_debit != total_credit:
        raise UnbalancedEntryError(f"debits {total_debit} != credits {total_credit}")

    # 3. Resolve account refs (raises if any ref is unknown/inactive).
    try:
        account_map = await resolve_account_ids(
            session, org_id, [line.account_ref for line in payload.lines]
        )
    except ValueError as exc:
        raise UnresolvedAccountError(str(exc)) from exc

    # 4. Period must be open.
    period = await resolve_or_create_period(session, org_id, payload.entry_date)
    if period.status != PeriodStatus.OPEN:
        raise PeriodClosedError(f"period {period.period_name} is {period.status.value}")

    # 5. Org base currency + cost-center code -> id resolution.
    org = await session.get(Organization, org_id)
    base_currency = org.base_currency if org is not None else "NGN"
    cc_codes = {line.cost_center for line in payload.lines if line.cost_center}
    cc_map: dict[str, uuid.UUID] = {}
    if cc_codes:
        cc_map = {
            code: cid
            for code, cid in (
                await session.execute(
                    select(CostCenter.code, CostCenter.id).where(
                        CostCenter.org_id == org_id,
                        CostCenter.code.in_(cc_codes),
                    )
                )
            ).all()
        }

    now = utc_now()
    entry = JournalEntry(
        org_id=org_id,
        entry_date=payload.entry_date,
        posting_date=now,
        description=payload.description,
        source_service=payload.source_service,
        source_type=payload.source_type,
        source_id=payload.source_id,
        idempotency_key=payload.idempotency_key,
        status=EntryStatus.POSTED,
        period_id=period.id,
        posted_by_user_id=posted_by_user_id,
        posted_by_service=posted_by_service or payload.source_service,
        entry_metadata=payload.metadata,
        posted_at=now,
    )
    for line in payload.lines:
        currency = (line.currency or base_currency).upper()
        entry.lines.append(
            JournalLine(
                org_id=org_id,
                account_id=account_map[line.account_ref],
                debit_minor=line.debit,
                credit_minor=line.credit,
                currency=currency,
                # NGN single-currency in Phase 1: base == face, no fx. Multi-
                # currency conversion lands with the FX phase.
                base_debit_minor=line.debit,
                base_credit_minor=line.credit,
                cost_center_id=(
                    cc_map.get(line.cost_center) if line.cost_center else None
                ),
                dimension_1=line.dimension_1,
                dimension_2=line.dimension_2,
                member_ref=line.member_ref,
                external_ref=line.external_ref,
                description=line.description,
            )
        )

    # 6. Insert. A savepoint turns the idempotency-race (concurrent same-key
    #    insert) into a clean replay instead of a 500.
    try:
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
    except IntegrityError:
        existing = await _get_by_idempotency(session, org_id, payload.idempotency_key)
        if existing is not None:
            return _result(existing, replay=True)
        raise

    # 7. Recompute balances for the affected accounts in this period.
    await recompute_account_balances(
        session, org_id, period.id, list(account_map.values())
    )

    # 8. Audit.
    session.add(
        AuditLog(
            org_id=org_id,
            actor_user_id=posted_by_user_id,
            actor_service=posted_by_service or payload.source_service,
            action=AuditActionType.ENTRY_POSTED,
            subject_type="journal_entry",
            subject_id=str(entry.id),
            payload={
                "source": f"{payload.source_service}:{payload.source_type}:{payload.source_id}",
                "idempotency_key": payload.idempotency_key,
            },
        )
    )
    await session.flush()

    # Auto-create revenue-recognition schedules for any deferred-revenue credit
    # lines (design §10). Local import avoids a posting<->recognition import
    # cycle. Only runs on a real post — replays returned at step 1.
    from services.ledger_service.services.recognition import (
        ensure_schedules_for_entry,
    )

    await ensure_schedules_for_entry(session, org_id, entry, payload)

    return _result(entry, replay=False)


async def reverse_entry(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    entry_id: uuid.UUID,
    reversed_by_user_id: Optional[uuid.UUID] = None,
    reason: Optional[str] = None,
) -> JournalEntryResult:
    """Post a reversing entry for `entry_id` and mark the original reversed.

    The reversal is a NEW entry with each line's debit/credit swapped, posted to
    the **current** open period (so it works even if the original's period is
    closed) and idempotent on f"ledger:reversal:{entry_id}". The original's lines
    are not deleted — original + reversal net (same period) or offset forward
    (later period). Runs in the caller's transaction.
    """
    original = await session.get(JournalEntry, entry_id)
    if original is None or original.org_id != org_id:
        raise EntryNotFoundError(f"entry {entry_id} not found")
    if original.status == EntryStatus.REVERSED:
        raise AlreadyReversedError(f"entry {entry_id} already reversed")

    original_lines = (
        (
            await session.execute(
                select(JournalLine).where(JournalLine.entry_id == entry_id)
            )
        )
        .scalars()
        .all()
    )

    now = utc_now()
    today = now.date()
    period = await resolve_or_create_period(session, org_id, today)
    if period.status != PeriodStatus.OPEN:
        raise PeriodClosedError(f"period {period.period_name} is {period.status.value}")

    rev = JournalEntry(
        org_id=org_id,
        entry_date=today,
        posting_date=now,
        description=(f"Reversal of {original.id}" + (f" — {reason}" if reason else "")),
        source_service="ledger",
        source_type="reversal",
        source_id=str(original.id),
        idempotency_key=f"ledger:reversal:{original.id}",
        status=EntryStatus.POSTED,
        period_id=period.id,
        reversal_of_entry_id=original.id,
        posted_by_user_id=reversed_by_user_id,
        posted_by_service="ledger",
        entry_metadata={"reason": reason} if reason else None,
        posted_at=now,
    )
    for line in original_lines:
        rev.lines.append(
            JournalLine(
                org_id=org_id,
                account_id=line.account_id,
                debit_minor=line.credit_minor,  # swap
                credit_minor=line.debit_minor,  # swap
                currency=line.currency,
                base_debit_minor=line.base_credit_minor,
                base_credit_minor=line.base_debit_minor,
                cost_center_id=line.cost_center_id,
                dimension_1=line.dimension_1,
                dimension_2=line.dimension_2,
                member_ref=line.member_ref,
                external_ref=line.external_ref,
                description=(
                    f"Reversal: {line.description}" if line.description else "Reversal"
                ),
            )
        )

    try:
        async with session.begin_nested():
            session.add(rev)
            await session.flush()
    except IntegrityError:
        existing = await _get_by_idempotency(
            session, org_id, f"ledger:reversal:{original.id}"
        )
        if existing is not None:
            return _result(existing, replay=True)
        raise

    original.status = EntryStatus.REVERSED
    original.reversed_by_entry_id = rev.id

    affected = {line.account_id for line in original_lines}
    await recompute_account_balances(session, org_id, period.id, affected)
    if original.period_id != period.id:
        await recompute_account_balances(session, org_id, original.period_id, affected)

    session.add(
        AuditLog(
            org_id=org_id,
            actor_user_id=reversed_by_user_id,
            actor_service="ledger",
            action=AuditActionType.ENTRY_REVERSED,
            subject_type="journal_entry",
            subject_id=str(original.id),
            payload={"reversal_entry_id": str(rev.id), "reason": reason},
        )
    )
    await session.flush()

    return _result(rev, replay=False)
