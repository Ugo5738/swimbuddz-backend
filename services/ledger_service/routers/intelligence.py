"""Read-only company-wide operational finance signals.

These are NOT posted financial statements. Each number carries its semantics;
no source record is modified, and missing valuation is flagged, not invented.
Finance permission is resolved from the Ledger user table, not JWT admin claims.
"""
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from libs.common.enums import PaymentStatus
from libs.common.datetime_utils import utc_now
from services.ledger_service.app.deps import get_ledger_db, require_ledger_role
from services.ledger_service.models.enums import LedgerRole
from services.academy_service.models.class_entitlement import AcademyClassAllocation, AcademyClassPlan
from services.academy_service.models.enrollment import Enrollment, EnrollmentInstallment
from services.academy_service.models.enums import EnrollmentStatus, InstallmentStatus
from services.members_service.models.club import ClubEnrollment
from services.payments_service.models.core import Payment
from services.payments_service.models.settlement import PaystackSettlement
from services.store_service.models import InventoryItem, Order, OrderStatus, ProductVariant
from services.store_service.models.procurement import (
    StoreInventoryValuation, StoreSupplierInvoice, StoreTradeReceivable,
)
from sqlalchemy import Numeric, and_, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/admin/finance", tags=["finance-intelligence"])


@router.get("/intelligence")
async def company_finance_intelligence(
    request: Request,
    _finance=Depends(require_ledger_role(LedgerRole.VIEWER)),
    session: AsyncSession = Depends(get_ledger_db),
):
    """Single-tenant cross-domain operational facts, carefully distinguished."""
    # Payment rows are checkout lifecycle facts, not bank settlement receipts.
    payment_rows = (await session.execute(select(
        Payment.payment_method,
        Payment.provider,
        func.count(Payment.id),
        func.coalesce(func.sum(cast(Payment.amount, Numeric(18, 2))), 0),
    ).where(
        Payment.status == PaymentStatus.PAID,
        Payment.currency == "NGN",
    ).group_by(Payment.payment_method, Payment.provider))).all()
    payments = [
        {"method": method or "unknown", "provider": provider or "unknown",
         "confirmed_count": int(count), "gross_ngn": str(total)}
        for method, provider, count, total in payment_rows
    ]
    settlement = (await session.execute(select(
        func.coalesce(func.sum(PaystackSettlement.gross_minor), 0),
        func.coalesce(func.sum(PaystackSettlement.net_minor), 0),
        func.coalesce(func.sum(PaystackSettlement.fees_minor), 0),
        func.count(PaystackSettlement.id),
    ).where(PaystackSettlement.ledger_posted.is_(True)))).one()
    unposted_settlement_count = int(await session.scalar(
        select(func.count(PaystackSettlement.id)).where(
            PaystackSettlement.ledger_posted.is_(False),
        )
    ) or 0)
    # Never extrapolate remaining classes for students missing reviewed plans.
    active = int(await session.scalar(select(func.count(Enrollment.id)).where(
        Enrollment.status == EnrollmentStatus.ENROLLED,
    )) or 0)
    plan_count = int(await session.scalar(select(func.count(AcademyClassPlan.id)).join(
        Enrollment, Enrollment.id == AcademyClassPlan.enrollment_id,
    ).where(Enrollment.status == EnrollmentStatus.ENROLLED)) or 0)
    plan_rows = (await session.execute(select(
        AcademyClassPlan.enrollment_id, AcademyClassPlan.purchased_classes,
        AcademyClassPlan.opening_consumed,
    ).join(Enrollment, Enrollment.id == AcademyClassPlan.enrollment_id).where(
        Enrollment.status == EnrollmentStatus.ENROLLED,
    ))).all()
    applied_rows = (await session.execute(select(
        AcademyClassAllocation.enrollment_id,
        func.count(AcademyClassAllocation.id),
    ).where(
        AcademyClassAllocation.status == "applied",
        AcademyClassAllocation.kind == "prepaid",
    ).group_by(AcademyClassAllocation.enrollment_id))).all()
    used = {eid: count for eid, count in applied_rows}
    remaining = sum(max(0, bought - opened - used.get(eid, 0))
                    for eid, bought, opened in plan_rows)
    installments_due_minor = int(await session.scalar(select(
        func.coalesce(func.sum(EnrollmentInstallment.amount), 0),
    ).where(
        EnrollmentInstallment.status.in_((InstallmentStatus.PENDING, InstallmentStatus.MISSED)),
    )) or 0)
    now = utc_now()
    club_active = int(await session.scalar(select(func.count(ClubEnrollment.id)).where(
        ClubEnrollment.status == "active",
        ClubEnrollment.starts_at <= now,
        ClubEnrollment.ends_at > now,
    )) or 0)
    store_rows = (await session.execute(select(
        Order.status, func.count(Order.id),
        func.coalesce(func.sum(Order.total_ngn), 0),
    ).group_by(Order.status))).all()
    store_orders = [{"status": status.value if hasattr(status, "value") else str(status),
                     "count": int(count), "gross_order_ngn": str(total)}
                    for status, count, total in store_rows]
    # Costing is complete only if all on-hand stock in that SKU has a verified
    # opening valuation and subsequent stock movements matched the cost ledger.
    valuation_rows = (await session.execute(select(
        InventoryItem.variant_id,
        InventoryItem.quantity_on_hand,
        StoreInventoryValuation.quantity,
        StoreInventoryValuation.total_cost_minor,
    ).outerjoin(
        StoreInventoryValuation, StoreInventoryValuation.variant_id == InventoryItem.variant_id,
    ))).all()
    valued_minor = 0
    unvalued_skus = 0
    for _variant, on_hand, costed_qty, cost_minor in valuation_rows:
        if costed_qty is None or costed_qty != on_hand:
            unvalued_skus += 1
        else:
            valued_minor += int(cost_minor)
    supplier_unpaid = int(await session.scalar(select(
        func.coalesce(func.sum(
            StoreSupplierInvoice.total_minor - StoreSupplierInvoice.verified_paid_minor,
        ), 0),
    )) or 0)
    approved_credit = int(await session.scalar(select(
        func.coalesce(func.sum(
            StoreTradeReceivable.amount_minor - StoreTradeReceivable.verified_paid_minor,
        ), 0),
    ).where(StoreTradeReceivable.status == "approved")) or 0)
    return {
        "as_of": now.isoformat(),
        "classification": "operational_planning_not_accounting_statement",
        "payments": {
            "successful_checkout_groups": payments,
            "posted_paystack_settlements": {
                "count": int(settlement[3]), "gross_minor": int(settlement[0]),
                "net_minor": int(settlement[1]), "fees_minor": int(settlement[2]),
            },
            "unposted_paystack_settlement_batches": unposted_settlement_count,
            "warning": "Provider-confirmed charges are not equal to bank cash; reconcile PSP settlements and transfers.",
        },
        "academy": {
            "active_enrollments": active, "reviewed_class_plans": plan_count,
            "enrollments_without_class_plan": max(active - plan_count, 0),
            "remaining_prepaid_classes_with_reviewed_plans": remaining,
            "pending_or_missed_installments_minor": installments_due_minor,
            "warning": "Remaining classes are obligations to deliver, not money owed. Review allocations and overdue instalments.",
        },
        "club": {
            "currently_active_enrollments": club_active,
            "future_service_costs_minor": None,
            "warning": "Club session/pool/coach cost forecast requires approved rate and session commitments.",
        },
        "store": {
            "order_status_totals": store_orders,
            "valued_inventory_minor": valued_minor if unvalued_skus == 0 else None,
            "partial_valued_inventory_minor": valued_minor,
            "unvalued_sku_count": unvalued_skus,
            "supplier_liabilities_minor": supplier_unpaid,
            "approved_credit_exposure_minor": approved_credit,
            "warning": "Inventory valuation is provisional until openings, sales, returns and adjustments reconcile to cost movements.",
        },
    }
