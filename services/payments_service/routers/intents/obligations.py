"""Member financial overview. Read-only, service-owned and fail-visible."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends
from libs.auth.dependencies import _service_role_jwt, get_current_user
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.db.session import get_async_db
from services.payments_service.models import Payment, PaymentPurpose, PaymentStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()
LAGOS = ZoneInfo("Africa/Lagos")


def classify(due_at: str | None, status: str, today: str) -> str:
    if status == "missed":
        return "overdue"
    if not due_at:
        return "due"
    try:
        day = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
        if day.tzinfo:
            date_value = day.astimezone(LAGOS).date().isoformat()
        else:
            date_value = day.date().isoformat()
    except ValueError:
        return "due"
    return (
        "overdue"
        if date_value < today
        else "due"
        if date_value == today
        else "upcoming"
    )


async def service_projection(client: httpx.AsyncClient, base: str, member_id: str):
    response = await client.get(
        f"{base}/financial-obligations/{member_id}",
        headers={"Authorization": f"Bearer {_service_role_jwt('payments')}"},
    )
    response.raise_for_status()
    return response.json()


def build_overview(academy, sessions, store, payments, failures, today):
    items = []
    pending = []
    checking = []
    unpriced = []

    def under_review(purpose: PaymentPurpose, item_id: str) -> bool:
        for payment in payments:
            if (
                payment.purpose != purpose
                or payment.status != PaymentStatus.PENDING_REVIEW
            ):
                continue
            if payment.payment_method != "manual_transfer":
                continue
            meta = payment.payment_metadata or {}
            if (
                purpose == PaymentPurpose.ACADEMY_COHORT
                and str(meta.get("installment_id")) == item_id
            ):
                return True
            if purpose == PaymentPurpose.SESSION_BOOKING and (
                str(meta.get("booking_id") or payment.session_booking_id) == item_id
            ):
                return True
        return False

    if academy is not None:
        for e in academy:
            eligible = e.get("status") in {
                "enrolled",
                "pending_approval",
                "dropout_pending",
            }
            if not eligible:
                continue
            rows = e.get("installments") or []
            if not rows and (
                e.get("uses_installments")
                or e.get("total_installments")
                or e.get("payment_status") != "paid"
            ):
                unpriced.append(
                    {
                        "source": "academy",
                        "id": e["id"],
                        "title": e["cohort_name"],
                        "href": f"/account/academy/enrollments/{e['id']}",
                    }
                )
            for row in rows:
                if (
                    row.get("status") not in {"missed", "pending"}
                    or row.get("amount_kobo", 0) <= 0
                ):
                    continue
                status = (
                    "checking"
                    if under_review(PaymentPurpose.ACADEMY_COHORT, row["id"])
                    else classify(row.get("due_at"), row["status"], today)
                )
                items.append(
                    {
                        "id": f"academy:{e['id']}:{row['id']}",
                        "source": "academy",
                        "title": e["cohort_name"],
                        "detail": f"Instalment {row['number']}",
                        "amount_kobo": row["amount_kobo"],
                        "due_at": row.get("due_at"),
                        "status": status,
                        "href": f"/account/academy/enrollments/{e['id']}",
                    }
                )
    if sessions is not None:
        for row in sessions:
            if row.get("amount_kobo", 0) <= 0:
                continue
            status = (
                "checking"
                if under_review(PaymentPurpose.SESSION_BOOKING, row["id"])
                else "due"
            )
            items.append(
                {
                    "id": f"session:{row['id']}",
                    "source": "session",
                    "title": row["name"],
                    "detail": "Swimming session fee",
                    "amount_kobo": row["amount_kobo"],
                    "due_at": row.get("starts_at"),
                    "status": status,
                    "href": f"/account/billing/sessions/{row['id']}",
                }
            )
    if store is not None:
        for row in store:
            pending.append(
                {
                    "id": f"store:{row['id']}",
                    "title": row["title"],
                    "amount_kobo": row["amount_kobo"],
                    "status": row["status"],
                    "href": f"/account/orders/{row['order_number']}",
                }
            )
    for item in items:
        if item["status"] == "checking":
            checking.append(item)
    return {
        "items": items,
        "optional_purchases": pending,
        "unpriced": unpriced,
        "due_kobo": sum(
            x["amount_kobo"] for x in items if x["status"] in {"due", "overdue"}
        ),
        "upcoming_kobo": sum(
            x["amount_kobo"] for x in items if x["status"] == "upcoming"
        ),
        "checking_kobo": sum(x["amount_kobo"] for x in checking),
        "sources_unavailable": failures,
        "complete": not failures and not unpriced,
        "as_of": datetime.now(LAGOS).isoformat(),
    }


@router.get("/me/obligations")
async def my_financial_obligations(
    user: AuthUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """A summary, not a payment initiation endpoint or universal receivables ledger."""
    settings = get_settings()
    urls = {
        "academy": settings.ACADEMY_SERVICE_URL + "/internal/academy",
        "sessions": settings.SESSIONS_SERVICE_URL + "/internal/sessions",
        "store": settings.STORE_SERVICE_URL + "/internal/store",
    }
    async with httpx.AsyncClient(timeout=8.0) as client:
        results = await asyncio.gather(
            *(service_projection(client, base, user.user_id) for base in urls.values()),
            return_exceptions=True,
        )
    projections = {}
    failures = []
    for name, result in zip(urls, results):
        if isinstance(result, BaseException) or not isinstance(result, list):
            failures.append(name)
            projections[name] = None
        else:
            projections[name] = result
    payments = (
        (
            await db.execute(
                select(Payment).where(Payment.member_auth_id == user.user_id)
            )
        )
        .scalars()
        .all()
    )
    today = datetime.now(LAGOS).date().isoformat()
    return build_overview(
        projections["academy"],
        projections["sessions"],
        projections["store"],
        payments,
        failures,
        today,
    )
