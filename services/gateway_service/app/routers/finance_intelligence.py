"""Gateway-aggregated cross-business Finance Intelligence endpoint (ADR-023).

Replaces the superseded raw-SQL projection inside ``ledger_service`` so that
``ledger_service`` stays 100% detachable and multi-tenant with zero knowledge
of SwimBuddz domain tables.

Authorization fails closed against ``ledger_service`` (forwarding the caller's
bearer token + optional ``X-Ledger-Org-Id`` header to ``/admin/finance/users/me``).
Global platform admin alone never substitutes for active Ledger membership.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from libs.common.config import get_settings
from libs.common.datetime_utils import utc_now
from libs.common.logging import get_logger
from libs.common.service_client.core import internal_get

logger = get_logger(__name__)

router = APIRouter(prefix="/admin/finance", tags=["finance-intelligence"])


async def _verify_ledger_viewer(request: Request) -> dict[str, Any]:
    """Authenticate the caller strictly against ledger_service (any active role)."""
    authorization = request.headers.get("authorization")
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in to view Finance Intelligence",
        )
    headers = {"Authorization": authorization}
    org_header = request.headers.get("x-ledger-org-id")
    if org_header:
        headers["X-Ledger-Org-Id"] = org_header

    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.LEDGER_SERVICE_URL}/admin/finance/users/me",
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Finance authorization service is unavailable",
        ) from exc

    if response.status_code in {401, 403, 404}:
        raise HTTPException(
            status_code=response.status_code,
            detail="Active Ledger membership required for Finance Intelligence",
        )
    if response.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Finance authorization service is unavailable",
        )
    data = response.json()
    if not isinstance(data, dict) or data.get("deactivated_at"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active Ledger membership required for Finance Intelligence",
        )
    return data


async def _fetch_domain_summary(
    domain: str,
    service_url: str,
    path: str,
    fallback: dict[str, Any],
) -> tuple[str, dict[str, Any], bool]:
    """Call one service-owned internal summary endpoint over authenticated HTTP."""
    try:
        resp = await internal_get(
            service_url=service_url,
            path=path,
            calling_service="gateway",
            timeout=10.0,
        )
        if resp.status_code == 200:
            payload = resp.json()
            if isinstance(payload, dict):
                return domain, payload, False
        logger.warning(
            "Finance Intelligence domain %s returned HTTP %s",
            domain,
            resp.status_code,
        )
    except Exception as exc:
        logger.warning("Finance Intelligence domain %s unreachable: %s", domain, exc)
    degraded = dict(fallback)
    degraded["warning"] = (
        f"{domain.title()} summary temporarily unavailable; figures omitted rather than estimated."
    )
    return domain, degraded, True


@router.get("/intelligence")
async def company_finance_intelligence(request: Request) -> dict[str, Any]:
    """Aggregate SwimBuddz operational finance signals via service-owned APIs."""
    await _verify_ledger_viewer(request)
    settings = get_settings()
    now = utc_now()

    calls = [
        _fetch_domain_summary(
            "payments",
            settings.PAYMENTS_SERVICE_URL,
            "/internal/payments/finance-summary",
            {
                "successful_checkout_groups": [],
                "posted_paystack_settlements": {
                    "count": 0,
                    "gross_minor": 0,
                    "net_minor": 0,
                    "fees_minor": 0,
                },
                "unposted_paystack_settlement_batches": 0,
            },
        ),
        _fetch_domain_summary(
            "academy",
            settings.ACADEMY_SERVICE_URL,
            "/internal/academy/finance-summary",
            {
                "active_enrollments": 0,
                "dropout_pending_enrollments": 0,
                "reviewed_class_plans": 0,
                "enrollments_without_class_plan": 0,
                "remaining_prepaid_classes_with_reviewed_plans": 0,
                "pending_or_missed_installments_minor": 0,
            },
        ),
        _fetch_domain_summary(
            "club",
            settings.MEMBERS_SERVICE_URL,
            "/internal/members/club-finance-summary",
            {
                "currently_active_enrollments": 0,
                "future_service_costs_minor": None,
            },
        ),
        _fetch_domain_summary(
            "store",
            settings.STORE_SERVICE_URL,
            "/internal/store/finance-summary",
            {
                "order_status_totals": [],
                "valued_inventory_minor": None,
                "partial_valued_inventory_minor": 0,
                "unvalued_sku_count": 0,
                "unvalued_fulfilled_orders_count": 0,
                "supplier_liabilities_minor": 0,
                "approved_credit_exposure_minor": 0,
            },
        ),
    ]

    results = await asyncio.gather(*calls)
    sections: dict[str, Any] = {}
    degraded_domains: list[str] = []
    for domain, payload, degraded in results:
        sections[domain] = payload
        if degraded:
            degraded_domains.append(domain)

    return {
        "as_of": now.isoformat(),
        "classification": "operational_planning_not_accounting_statement",
        "degraded_domains": degraded_domains,
        "payments": sections["payments"],
        "academy": sections["academy"],
        "club": sections["club"],
        "store": sections["store"],
    }
