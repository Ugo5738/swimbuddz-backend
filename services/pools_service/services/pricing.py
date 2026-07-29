"""Resolve effective rates into an editable session cost quote."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.pools_service.models import (
    OperatingArea,
    OperatingCostRate,
    Pool,
    PoolRate,
)
from services.pools_service.schemas.pricing import CostQuoteRequest


class PricingAmbiguityError(ValueError):
    """Raised when equally specific active rates overlap."""


def _local_start(request: CostQuoteRequest) -> datetime:
    try:
        zone = ZoneInfo(request.timezone)
    except Exception as exc:
        raise ValueError(f"Unknown timezone: {request.timezone}") from exc
    value = request.starts_at
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def _rate_applies(rate, request: CostQuoteRequest, local_start: datetime) -> bool:
    on = local_start.date()
    if rate.activity_scope not in {"all", request.activity_scope}:
        return False
    if on < rate.effective_from:
        return False
    if rate.effective_to and on > rate.effective_to:
        return False
    if rate.day_of_week is not None and rate.day_of_week != local_start.weekday():
        return False
    local_time = local_start.time().replace(tzinfo=None)
    if rate.starts_after and local_time < rate.starts_after:
        return False
    if rate.ends_before and local_time > rate.ends_before:
        return False
    return True


def _condition_score(rate, request: CostQuoteRequest) -> int:
    return (
        (20 if rate.activity_scope == request.activity_scope else 0)
        + (8 if rate.day_of_week is not None else 0)
        + (4 if rate.starts_after is not None else 0)
        + (4 if rate.ends_before is not None else 0)
    )


def _choose_rate(rates: Iterable, score) -> object | None:
    ranked = sorted(((score(rate), rate) for rate in rates), key=lambda row: row[0])
    if not ranked:
        return None
    highest = ranked[-1][0]
    winners = [rate for value, rate in ranked if value == highest]
    if len(winners) > 1:
        ids = ", ".join(str(rate.id) for rate in winners)
        raise PricingAmbiguityError(f"Overlapping rates need review: {ids}")
    return winners[0]


async def _area_chain(
    db: AsyncSession,
    area_id,
) -> list[OperatingArea]:
    chain: list[OperatingArea] = []
    current_id = area_id
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        area = (
            await db.execute(
                select(OperatingArea).where(OperatingArea.id == current_id)
            )
        ).scalar_one_or_none()
        if area is None:
            break
        chain.append(area)
        current_id = area.parent_id
    return chain


def _quantity(
    charge_basis: str,
    request: CostQuoteRequest,
    minimum_quantity: int,
) -> Decimal:
    duration_hours = Decimal(
        str(max((request.ends_at - request.starts_at).total_seconds() / 3600, 0))
    )
    values = {
        "per_attendee": Decimal(request.expected_attendees),
        "per_staff": Decimal(request.expected_staff),
        "per_hour": duration_hours,
        "per_lane": Decimal(request.lanes),
        "flat_session": Decimal(1),
    }
    return max(values[charge_basis], Decimal(minimum_quantity or 1))


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _line(rate, request: CostQuoteRequest, *, category: str, source: str) -> dict:
    quantity = _quantity(rate.charge_basis, request, rate.minimum_quantity)
    unit_naira = Decimal(rate.amount_kobo) / Decimal(100)
    total_naira = _money(unit_naira * quantity)
    return {
        "category": category,
        "description": rate.description or category.replace("_", " ").title(),
        "charge_basis": rate.charge_basis,
        "unit_cost_naira": float(_money(unit_naira)),
        "quantity": float(quantity),
        "total_cost_naira": float(total_naira),
        "source_rate_type": source,
        "source_rate_id": rate.id,
    }


async def build_cost_quote(
    db: AsyncSession,
    request: CostQuoteRequest,
) -> dict:
    """Build a rate-derived quote without persisting a financial snapshot."""
    pool = (
        await db.execute(select(Pool).where(Pool.id == request.pool_id))
    ).scalar_one_or_none()
    if pool is None:
        raise LookupError("Pool not found")

    local_start = _local_start(request)
    area_chain = await _area_chain(db, pool.operating_area_id)
    area_ids = [area.id for area in area_chain]
    warnings: list[str] = []
    lines: list[dict] = []

    pool_candidates = (
        (
            await db.execute(
                select(PoolRate).where(
                    PoolRate.pool_id == pool.id,
                    PoolRate.is_active.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    pool_candidates = [
        rate for rate in pool_candidates if _rate_applies(rate, request, local_start)
    ]
    selected_pool_rate = _choose_rate(
        pool_candidates,
        lambda rate: _condition_score(rate, request),
    )
    if selected_pool_rate is None:
        warnings.append("No active pool rate matched this activity and date.")
    else:
        lines.append(
            _line(
                selected_pool_rate,
                request,
                category="pool",
                source="pool_rate",
            )
        )

    cost_query = select(OperatingCostRate).where(
        OperatingCostRate.is_active.is_(True),
        or_(
            OperatingCostRate.pool_id == pool.id,
            OperatingCostRate.operating_area_id.in_(area_ids)
            if area_ids
            else OperatingCostRate.operating_area_id.is_(None),
            (
                OperatingCostRate.pool_id.is_(None)
                & OperatingCostRate.operating_area_id.is_(None)
            ),
        ),
    )
    cost_candidates = (await db.execute(cost_query)).scalars().all()
    cost_candidates = [
        rate for rate in cost_candidates if _rate_applies(rate, request, local_start)
    ]
    by_category: dict[str, list[OperatingCostRate]] = defaultdict(list)
    for rate in cost_candidates:
        by_category[rate.category].append(rate)

    area_closeness = {
        area.id: len(area_chain) - index for index, area in enumerate(area_chain)
    }

    def cost_score(rate: OperatingCostRate) -> int:
        scope_score = 1000 if rate.pool_id == pool.id else 0
        if rate.operating_area_id:
            scope_score += 100 + area_closeness.get(rate.operating_area_id, 0)
        return scope_score + _condition_score(rate, request)

    for category, candidates in sorted(by_category.items()):
        selected = _choose_rate(candidates, cost_score)
        if selected is not None:
            lines.append(
                _line(
                    selected,
                    request,
                    category=category,
                    source="operating_cost_rate",
                )
            )

    total = _money(
        sum(
            (Decimal(str(line["total_cost_naira"])) for line in lines),
            Decimal(0),
        )
    )
    per_attendee = _money(total / Decimal(request.expected_attendees))
    return {
        "pool_id": pool.id,
        "operating_area_id": pool.operating_area_id,
        "activity_scope": request.activity_scope,
        "currency": area_chain[0].currency if area_chain else "NGN",
        "expected_attendees": request.expected_attendees,
        "lines": lines,
        "estimated_total_cost_naira": float(total),
        "estimated_cost_per_attendee_naira": float(per_attendee),
        "warnings": warnings,
    }
