"""Session commercial-rate compatibility layer.

Phase 1 keeps the legacy Session price columns readable/writable, but mirrors
those values into explicit SessionRate rows and prefers SessionRate when
resolving a member's price. This lets us migrate callers incrementally without
changing admission semantics or rewriting historical bookings.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.session_access import SessionAccessDecision
from services.sessions_service.models import Session, SessionRate


def _session_type(session: Session) -> str:
    value = getattr(session.session_type, "value", session.session_type)
    return str(value).lower()


def legacy_rate_specs(session: Session) -> list[dict]:
    """Translate current Session fields into explicit audience-rate rows."""
    kind = _session_type(session)
    pool_fee = int(getattr(session, "pool_fee", 0) or 0)
    guest_fee = getattr(session, "guest_fee_kobo", None)
    community_fee = getattr(session, "community_dropin_fee_kobo", None)
    specs: list[dict] = []

    def add(
        *,
        audience: str,
        rate_code: str,
        label: str,
        amount_kobo: int,
        rate_mode: str = "fixed",
        access_source: str | None = None,
        priority: int = 100,
    ) -> None:
        specs.append(
            {
                "audience": audience,
                "rate_code": rate_code,
                "label": label,
                "amount_kobo": max(0, int(amount_kobo)),
                "rate_mode": rate_mode,
                "access_source": access_source,
                "priority": priority,
            }
        )

    if kind == "club":
        add(
            audience="club",
            access_source="club_enrollment",
            rate_code="club_included",
            label="Included in Club quarter",
            amount_kobo=0,
            rate_mode="included",
            priority=10,
        )
        add(
            audience="club",
            access_source="club_transition",
            rate_code="club_transition",
            label="Transition session rate",
            amount_kobo=pool_fee,
            priority=20,
        )
        add(
            audience="club",
            rate_code="club_member",
            label="Club session rate",
            amount_kobo=pool_fee,
            priority=100,
        )
        if getattr(session, "allows_community_dropins", False) and community_fee is not None:
            add(
                audience="community",
                access_source="community_dropin",
                rate_code="community_dropin",
                label="Community drop-in rate",
                amount_kobo=int(community_fee),
                priority=10,
            )
    elif kind == "cohort_class":
        paid_extra = getattr(session, "cohort_fee_mode", "included") == "paid_extra"
        add(
            audience="academy",
            rate_code="academy_extra" if paid_extra else "academy_included",
            label="Extra cohort class" if paid_extra else "Included in Academy tuition",
            amount_kobo=pool_fee if paid_extra else 0,
            rate_mode="fixed" if paid_extra else "included",
        )
    elif kind == "event":
        add(
            audience="club",
            rate_code="event_club",
            label="Club event rate",
            amount_kobo=pool_fee,
        )
        add(
            audience="academy",
            rate_code="event_academy",
            label="Academy event rate",
            amount_kobo=pool_fee,
        )
        add(
            audience="community",
            rate_code="event_community",
            label="Community member rate",
            amount_kobo=int(community_fee) if community_fee is not None else pool_fee,
        )
    else:  # community and any legacy fallback
        add(
            audience="community",
            rate_code="community_member",
            label="Community member rate",
            amount_kobo=pool_fee,
        )

    if guest_fee is not None:
        add(
            audience="guest",
            rate_code="guest",
            label="Guest / non-member rate",
            amount_kobo=int(guest_fee),
        )

    return specs


async def sync_legacy_session_rates(db: AsyncSession, session: Session) -> None:
    """Mirror legacy pricing fields into legacy_bridge SessionRate rows.

    Admin-authored/non-bridge rates are never changed here.
    """
    specs = legacy_rate_specs(session)
    existing = list(
        (
            await db.execute(
                select(SessionRate).where(
                    SessionRate.session_id == session.id,
                    SessionRate.source == "legacy_bridge",
                )
            )
        ).scalars()
    )
    by_code = {row.rate_code: row for row in existing}
    wanted = {spec["rate_code"] for spec in specs}

    for spec in specs:
        row = by_code.get(spec["rate_code"])
        if row is None:
            row = SessionRate(
                session_id=session.id,
                source="legacy_bridge",
                is_active=True,
                **spec,
            )
            db.add(row)
        else:
            for key, value in spec.items():
                setattr(row, key, value)
            row.is_active = True

    obsolete_ids = [row.id for row in existing if row.rate_code not in wanted]
    if obsolete_ids:
        await db.execute(delete(SessionRate).where(SessionRate.id.in_(obsolete_ids)))


def apply_explicit_rate(
    decision: SessionAccessDecision,
    rows: Iterable[SessionRate],
) -> SessionAccessDecision:
    """Override only pricing fields; admission/access_source remains untouched."""
    audience = decision.pricing_audience
    if not audience or decision.access_source == "confirmed_booking":
        return decision

    candidates = [
        row
        for row in rows
        if row.is_active
        and row.audience == audience
        and (row.access_source is None or row.access_source == decision.access_source)
    ]
    if not candidates:
        return decision

    candidates.sort(
        key=lambda row: (
            0 if row.access_source == decision.access_source else 1,
            row.priority,
            str(row.id),
        )
    )
    rate = candidates[0]
    amount = 0 if rate.rate_mode == "included" else int(rate.amount_kobo)
    return replace(
        decision,
        fee_amount_kobo=amount,
        price_label=rate.label,
        pricing_source="session_rate",
        rate_code=rate.rate_code,
        rate_id=str(rate.id),
    )


async def apply_session_rate(
    db: AsyncSession,
    session: Session,
    decision: SessionAccessDecision,
) -> SessionAccessDecision:
    rows = list(
        (
            await db.execute(
                select(SessionRate).where(
                    SessionRate.session_id == session.id,
                    SessionRate.is_active.is_(True),
                )
            )
        ).scalars()
    )
    return apply_explicit_rate(decision, rows)


async def rates_by_session(
    db: AsyncSession, session_ids: Iterable
) -> dict[str, list[SessionRate]]:
    ids = list(dict.fromkeys(session_ids))
    if not ids:
        return {}
    rows = list(
        (
            await db.execute(
                select(SessionRate).where(
                    SessionRate.session_id.in_(ids),
                    SessionRate.is_active.is_(True),
                )
            )
        ).scalars()
    )
    result: dict[str, list[SessionRate]] = {}
    for row in rows:
        result.setdefault(str(row.session_id), []).append(row)
    return result
