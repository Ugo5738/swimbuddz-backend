"""Read-only company-wide operational finance signals.

These are NOT posted financial statements. Each number carries its semantics;
no source record is modified, and missing valuation is flagged, not invented.
Finance permission is resolved from the Ledger user table, not JWT admin claims.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from libs.common.datetime_utils import utc_now
from services.ledger_service.app.deps import get_ledger_db, require_ledger_role
from services.ledger_service.models.enums import LedgerRole

router = APIRouter(prefix="/admin/finance", tags=["finance-intelligence"])


@router.get("/intelligence")
async def company_finance_intelligence(
    _finance=Depends(require_ledger_role(LedgerRole.VIEWER)),
    session: AsyncSession = Depends(get_ledger_db),
):
    """Single-tenant cross-domain operational facts, carefully distinguished."""
    # This report reads the deployment's shared database. Keep projections
    # local so the Ledger image can start without importing other services.
    payment_rows = await session.execute(
        text("""
            SELECT payment_method, provider, count(id),
                   coalesce(sum(CAST(amount AS numeric(18, 2))), 0)
            FROM payments
            WHERE status = 'paid' AND currency = 'NGN'
            GROUP BY payment_method, provider
        """)
    )
    payments = [
        {
            "method": method or "unknown",
            "provider": provider or "unknown",
            "confirmed_count": count,
            "gross_ngn": str(total),
        }
        for method, provider, count, total in payment_rows
    ]
    settlement = (
        (
            await session.execute(
                text("""
                SELECT count(id) FILTER (WHERE ledger_posted IS TRUE) AS posted_count,
                       count(id) FILTER (WHERE ledger_posted IS FALSE) AS unposted_count,
                       coalesce(sum(gross_minor) FILTER (WHERE ledger_posted IS TRUE), 0) AS gross_minor,
                       coalesce(sum(net_minor) FILTER (WHERE ledger_posted IS TRUE), 0) AS net_minor,
                       coalesce(sum(fees_minor) FILTER (WHERE ledger_posted IS TRUE), 0) AS fees_minor
                FROM paystack_settlements
            """)
            )
        )
        .mappings()
        .one()
    )
    active = await session.scalar(
        text("SELECT count(id) FROM enrollments WHERE status = 'enrolled'")
    )
    # Remaining classes are computed only for reviewed plans. A reversed
    # allocation or a class charged separately does not reduce this balance.
    plan_count, remaining = (
        await session.execute(
            text("""
                WITH prepaid_usage AS (
                    SELECT enrollment_id, count(id) AS used
                    FROM academy_class_allocations
                    WHERE status = 'applied' AND kind = 'prepaid'
                    GROUP BY enrollment_id
                )
                SELECT count(plan.id),
                       coalesce(sum(greatest(
                           0,
                           plan.purchased_classes - plan.opening_consumed
                             - coalesce(prepaid_usage.used, 0)
                       )), 0)
                FROM academy_class_plans AS plan
                JOIN enrollments ON enrollments.id = plan.enrollment_id
                LEFT JOIN prepaid_usage USING (enrollment_id)
                WHERE enrollments.status = 'enrolled'
            """)
        )
    ).one()
    installments_due_minor = await session.scalar(
        text("""
            SELECT coalesce(sum(amount), 0)
            FROM enrollment_installments
            WHERE status IN ('pending', 'missed')
        """)
    )
    now = utc_now()
    club_active = await session.scalar(
        text("""
            SELECT count(id)
            FROM club_enrollments
            WHERE status = 'active' AND starts_at <= :now AND ends_at > :now
        """),
        {"now": now},
    )
    store_rows = await session.execute(
        text("""
            SELECT status, count(id), coalesce(sum(total_ngn), 0)
            FROM store_orders
            GROUP BY status
        """)
    )
    store_orders = [
        {
            "status": status,
            "count": count,
            "gross_order_ngn": str(total),
        }
        for status, count, total in store_rows
    ]
    # Count stock as valued only when the recorded cost covers the on-hand quantity.
    valued_minor, unvalued_skus = (
        await session.execute(
            text("""
                SELECT coalesce(sum(valuation.total_cost_minor) FILTER (
                           WHERE valuation.quantity = inventory.quantity_on_hand
                       ), 0),
                       count(*) FILTER (
                           WHERE valuation.quantity IS DISTINCT FROM inventory.quantity_on_hand
                       )
                FROM store_inventory_items AS inventory
                LEFT JOIN store_inventory_valuations AS valuation USING (variant_id)
            """)
        )
    ).one()
    supplier_unpaid = await session.scalar(
        text("""
            SELECT coalesce(sum(total_minor - verified_paid_minor), 0)
            FROM store_supplier_invoices
        """)
    )
    approved_credit = await session.scalar(
        text("""
            SELECT coalesce(sum(amount_minor - verified_paid_minor), 0)
            FROM store_trade_receivables
            WHERE status = 'approved'
        """)
    )
    return {
        "as_of": now.isoformat(),
        "classification": "operational_planning_not_accounting_statement",
        "payments": {
            "successful_checkout_groups": payments,
            "posted_paystack_settlements": {
                "count": settlement["posted_count"],
                "gross_minor": int(settlement["gross_minor"]),
                "net_minor": int(settlement["net_minor"]),
                "fees_minor": int(settlement["fees_minor"]),
            },
            "unposted_paystack_settlement_batches": settlement["unposted_count"],
            "warning": "Provider-confirmed charges are not equal to bank cash; reconcile PSP settlements and transfers.",
        },
        "academy": {
            "active_enrollments": active,
            "reviewed_class_plans": plan_count,
            "enrollments_without_class_plan": max(active - plan_count, 0),
            "remaining_prepaid_classes_with_reviewed_plans": int(remaining),
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
            "valued_inventory_minor": int(valued_minor) if unvalued_skus == 0 else None,
            "partial_valued_inventory_minor": int(valued_minor),
            "unvalued_sku_count": unvalued_skus,
            "supplier_liabilities_minor": supplier_unpaid,
            "approved_credit_exposure_minor": approved_credit,
            "warning": "Inventory valuation is provisional until openings, sales, returns and adjustments reconcile to cost movements.",
        },
    }
