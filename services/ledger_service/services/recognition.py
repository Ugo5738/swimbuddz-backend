"""Revenue recognition engine (design §10) — the deferred-revenue waterfall.

Flow:
  * `ensure_schedules_for_entry` — called from `post_entry` for a freshly posted
    entry; creates a recognition schedule for each deferred-revenue **credit**
    line (idempotent). No-op for non-deferred entries.
  * `backfill_schedules` — one-off: creates schedules for deferred lines that
    were posted *before* the hook existed (e.g. the historical payment backfill).
  * `run_due_recognition` — walks active schedules and posts the earned delta
    (`DR deferred_revenue_* / CR revenue_*`) straight-line by elapsed days,
    idempotent per (schedule, calendar month), advancing `recognized_minor`.

RECOGNITION POLICY (durations) is a **business policy** (confirmed 2026-06):
  * community = 365 days — flat annual entry fee.
  * academy = 84 days — the typical 12-week "beginner freestyle" cohort (most
    popular). Cohorts vary by goal; exact per-cohort length lands when
    academy_service drives recognition off the real cohort dates (design §8.4 /
    roadmap R2). Straight-line keeps the total correct; only the month-by-month
    split is approximate.
  * club = the member's selected term. payments_service stores the period
    (quarterly/6mo/annual) as `months` in payment_metadata; the emitter converts
    it to metadata["recognition_days"] so the schedule spans the exact membership
    window. 90d (quarterly, the default cycle) is only the fallback for entries
    lacking that hint. Free post-cohort club months are ₦0, so they never create
    a deferred balance.
  * session_bundle (per-attendance) and events (at event date) are
    **delivery-based** — recognised via their domain service in R2, not here.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Optional

from services.ledger_service.models import (
    ChartOfAccounts,
    JournalEntry,
    JournalLine,
    RevenueRecognitionSchedule,
)
from services.ledger_service.models.enums import (
    EntryStatus,
    RecognitionMethod,
    RecognitionStatus,
)
from services.ledger_service.schemas.journal import JournalEntryCreate, JournalLineInput
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

# deferred account ref -> (method, duration_days). See the module docstring for
# the business rationale. Club is deliberately absent (member-selected terms).
RECOGNITION_POLICY: dict[str, tuple[RecognitionMethod, int]] = {
    "deferred_revenue_community": (RecognitionMethod.STRAIGHT_LINE, 365),
    "deferred_revenue_academy": (RecognitionMethod.STRAIGHT_LINE, 84),
    # Club: the emitter passes the real term via metadata["recognition_days"]
    # (the member's quarterly/6mo/annual selection). 90d is the fallback default.
    "deferred_revenue_club": (RecognitionMethod.STRAIGHT_LINE, 90),
}

# deferred account ref -> the revenue account it recognises into.
DEFERRED_TO_REVENUE: dict[str, str] = {
    "deferred_revenue_community": "revenue_community",
    "deferred_revenue_club": "revenue_club_membership",
    "deferred_revenue_academy": "revenue_academy",
}


def recognizable_amount(
    total_minor: int,
    recognized_minor: int,
    start_date: date,
    end_date: date,
    as_of: date,
) -> int:
    """Straight-line earned-to-date minus already-recognised, in minor units.

    Pure + monotonic: never negative (we don't un-recognise), and the final step
    (``as_of >= end_date``) recognises exactly the remainder, so there's no
    rounding drift left in deferred at the end.
    """
    if as_of < start_date:
        earned_to_date = 0
    elif as_of >= end_date or end_date <= start_date:
        earned_to_date = total_minor
    else:
        elapsed = (as_of - start_date).days
        duration = (end_date - start_date).days
        earned_to_date = (total_minor * elapsed) // duration
    return max(0, earned_to_date - recognized_minor)


async def _resolve_deferred_policies(
    session: AsyncSession,
    org_id: uuid.UUID,
    refs: list[str],
) -> dict[str, tuple[RecognitionMethod, int, str]]:
    """Resolve ``deferred_ref -> (method, default_days, revenue_ref)`` for an org.

    Checks ``ChartOfAccounts.account_metadata`` first (``recognition_target_ref``
    + ``default_recognition_days``) so any tenant / CoA vertical can define
    deferred-revenue schedules purely in data. Falls back to legacy
    ``RECOGNITION_POLICY`` / ``DEFERRED_TO_REVENUE`` for already-seeded orgs.
    """
    resolved: dict[str, tuple[RecognitionMethod, int, str]] = {}
    if not refs:
        return resolved

    maps_to = ChartOfAccounts.account_metadata["maps_to"].astext
    rows = (
        await session.execute(
            select(
                maps_to, ChartOfAccounts.code, ChartOfAccounts.account_metadata
            ).where(
                ChartOfAccounts.org_id == org_id,
                ChartOfAccounts.is_active.is_(True),
                (maps_to.in_(refs) | ChartOfAccounts.code.in_(refs)),
            )
        )
    ).all()

    meta_by_ref: dict[str, dict] = {}
    for m, code, meta in rows:
        if isinstance(meta, dict):
            if m:
                meta_by_ref[m] = meta
            meta_by_ref[code] = meta

    for ref in refs:
        meta = meta_by_ref.get(ref) or {}
        target_ref = meta.get("recognition_target_ref")
        default_days = meta.get("default_recognition_days")
        if (
            isinstance(target_ref, str)
            and target_ref
            and isinstance(default_days, int)
            and default_days > 0
        ):
            resolved[ref] = (RecognitionMethod.STRAIGHT_LINE, default_days, target_ref)
        elif ref in RECOGNITION_POLICY and ref in DEFERRED_TO_REVENUE:
            method, fallback_days = RECOGNITION_POLICY[ref]
            resolved[ref] = (method, fallback_days, DEFERRED_TO_REVENUE[ref])
    return resolved


async def _create_schedule(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    source_service: str,
    source_type: str,
    source_id: str,
    origin_entry_id: Optional[uuid.UUID],
    deferred_ref: str,
    total_minor: int,
    start: date,
    dimension_1: Optional[str],
    member_ref: Optional[str],
    currency: str,
    duration_days: Optional[int] = None,
    revenue_ref: Optional[str] = None,
    method: Optional[RecognitionMethod] = None,
    default_days: Optional[int] = None,
) -> bool:
    """Insert one schedule; idempotent on the source unique constraint.

    `duration_days` overrides the per-account policy default — the emitter passes
    the real term (e.g. a club member's selected period). Returns True if created,
    False if it already existed.
    """
    if method is None or default_days is None:
        fallback_method, fallback_days = RECOGNITION_POLICY[deferred_ref]
        method = method or fallback_method
        default_days = default_days or fallback_days
    target_revenue_ref = revenue_ref or DEFERRED_TO_REVENUE[deferred_ref]
    days = duration_days if duration_days and duration_days > 0 else default_days
    schedule = RevenueRecognitionSchedule(
        org_id=org_id,
        source_service=source_service,
        source_type=source_type,
        source_id=source_id,
        origin_entry_id=origin_entry_id,
        deferred_account_ref=deferred_ref,
        revenue_account_ref=target_revenue_ref,
        dimension_1=dimension_1,
        member_ref=member_ref,
        currency=currency or "NGN",
        total_minor=total_minor,
        recognized_minor=0,
        method=method,
        start_date=start,
        end_date=start + timedelta(days=days),
        status=RecognitionStatus.ACTIVE,
    )
    try:
        async with session.begin_nested():
            session.add(schedule)
            await session.flush()
        return True
    except IntegrityError:
        return False  # already scheduled for this source — idempotent no-op


async def ensure_schedules_for_entry(
    session: AsyncSession,
    org_id: uuid.UUID,
    entry: JournalEntry,
    payload: JournalEntryCreate,
) -> None:
    """Create recognition schedules for the entry's deferred-revenue credits.

    Called from `post_entry` on a real (non-replay) post. Uses account metadata
    (``recognition_target_ref`` / ``default_recognition_days``) with fallback to
    ``RECOGNITION_POLICY`` so any org's deferred accounts are supported.
    """
    credit_refs = [line.account_ref for line in payload.lines if line.credit > 0]
    policies = await _resolve_deferred_policies(session, org_id, credit_refs)
    if not policies:
        return
    recognition_days = (payload.metadata or {}).get("recognition_days")
    for line in payload.lines:
        if line.credit <= 0 or line.account_ref not in policies:
            continue
        method, default_days, revenue_ref = policies[line.account_ref]
        await _create_schedule(
            session,
            org_id,
            source_service=payload.source_service,
            source_type=payload.source_type,
            source_id=payload.source_id or str(entry.id),
            origin_entry_id=entry.id,
            deferred_ref=line.account_ref,
            total_minor=line.credit,
            start=entry.entry_date,
            dimension_1=line.dimension_1,
            member_ref=line.member_ref,
            currency=line.currency or "NGN",
            duration_days=recognition_days,
            revenue_ref=revenue_ref,
            method=method,
            default_days=default_days,
        )


async def backfill_schedules(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Create schedules for deferred-revenue credits posted before the hook.

    Scans posted entries' deferred credit lines (matched via CoA metadata
    ``recognition_target_ref`` or legacy ``RECOGNITION_POLICY``) and ensures a
    schedule exists for each. Idempotent. Returns newly-created count.
    """
    maps_to = ChartOfAccounts.account_metadata["maps_to"].astext
    target_ref = ChartOfAccounts.account_metadata["recognition_target_ref"].astext
    rows = (
        await session.execute(
            select(JournalEntry, JournalLine, maps_to, ChartOfAccounts.account_metadata)
            .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
            .join(ChartOfAccounts, ChartOfAccounts.id == JournalLine.account_id)
            .where(
                JournalEntry.org_id == org_id,
                JournalEntry.status == EntryStatus.POSTED,
                JournalLine.credit_minor > 0,
                (
                    target_ref.is_not(None)
                    | maps_to.in_(list(RECOGNITION_POLICY.keys()))
                ),
            )
        )
    ).all()

    created = 0
    for entry, line, deferred_ref, meta in rows:
        meta = meta if isinstance(meta, dict) else {}
        rev_ref = meta.get("recognition_target_ref") or DEFERRED_TO_REVENUE.get(
            deferred_ref
        )
        def_days = meta.get("default_recognition_days")
        if not rev_ref:
            continue
        if isinstance(def_days, int) and def_days > 0:
            method, fallback_days = RecognitionMethod.STRAIGHT_LINE, def_days
        elif deferred_ref in RECOGNITION_POLICY:
            method, fallback_days = RECOGNITION_POLICY[deferred_ref]
        else:
            continue
        was_created = await _create_schedule(
            session,
            org_id,
            source_service=entry.source_service,
            source_type=entry.source_type,
            source_id=entry.source_id or str(entry.id),
            origin_entry_id=entry.id,
            deferred_ref=deferred_ref,
            total_minor=line.credit_minor,
            start=entry.entry_date,
            dimension_1=line.dimension_1,
            member_ref=line.member_ref,
            currency=line.currency or "NGN",
            duration_days=(entry.entry_metadata or {}).get("recognition_days"),
            revenue_ref=rev_ref,
            method=method,
            default_days=fallback_days,
        )
        created += int(was_created)
    return created


async def run_due_recognition(
    session: AsyncSession, org_id: uuid.UUID, as_of: date
) -> dict:
    """Post the earned delta for every active schedule, as of `as_of`.

    One recognition entry per (schedule, calendar month) via the idempotency key,
    so re-runs in the same month are no-ops. `recognized_minor` advances only on
    a real post (never on an idempotent replay), keeping it consistent with the
    posted entries. Runs in the caller's transaction.
    """
    from services.ledger_service.services.posting import post_entry  # avoid cycle

    schedules = (
        (
            await session.execute(
                select(RevenueRecognitionSchedule).where(
                    RevenueRecognitionSchedule.org_id == org_id,
                    RevenueRecognitionSchedule.status == RecognitionStatus.ACTIVE,
                )
            )
        )
        .scalars()
        .all()
    )

    period_tag = f"{as_of.year:04d}-{as_of.month:02d}"
    posted = 0
    recognized_total = 0
    for s in schedules:
        delta = recognizable_amount(
            s.total_minor, s.recognized_minor, s.start_date, s.end_date, as_of
        )
        if delta <= 0:
            if as_of >= s.end_date and s.recognized_minor >= s.total_minor:
                s.status = RecognitionStatus.COMPLETED
            continue

        payload = JournalEntryCreate(
            idempotency_key=f"ledger:recognition:{s.id}:{period_tag}",
            entry_date=as_of,
            description=f"Revenue recognition — {s.revenue_account_ref} ({period_tag})",
            source_service="ledger",
            source_type="revenue_recognition",
            source_id=str(s.id),
            metadata={"schedule_id": str(s.id), "origin_source_id": s.source_id},
            lines=[
                JournalLineInput(
                    account_ref=s.deferred_account_ref,
                    debit=delta,
                    currency=s.currency,
                ),
                JournalLineInput(
                    account_ref=s.revenue_account_ref,
                    credit=delta,
                    currency=s.currency,
                    dimension_1=s.dimension_1,
                    member_ref=s.member_ref,
                ),
            ],
        )
        result = await post_entry(
            session, org_id=org_id, payload=payload, posted_by_service="ledger"
        )
        if not result.idempotent_replay:
            s.recognized_minor += delta
            if s.recognized_minor >= s.total_minor:
                s.status = RecognitionStatus.COMPLETED
            posted += 1
            recognized_total += delta

    return {
        "active_schedules": len(schedules),
        "entries_posted": posted,
        "recognized_minor": recognized_total,
    }
