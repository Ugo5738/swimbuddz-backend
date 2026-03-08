"""Admin store reports: sales summary, inventory, supplier performance."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, Query
from libs.auth.dependencies import require_admin
from libs.auth.models import AuthUser
from libs.db.session import get_async_db
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from services.store_service.models import (
    InventoryItem,
    Order,
    OrderItem,
    OrderStatus,
    ProductVariant,
    Supplier,
    SupplierPayout,
)

router = APIRouter(tags=["admin-store"])


# ============================================================================
# RESPONSE SCHEMAS (kept local to this module)
# ============================================================================


class SalesSummary(BaseModel):
    period_start: datetime
    period_end: datetime
    total_orders: int
    paid_orders: int
    cancelled_orders: int
    total_revenue_ngn: Decimal
    total_discount_ngn: Decimal
    total_delivery_fees_ngn: Decimal
    average_order_value_ngn: Decimal
    total_items_sold: int
    total_bubbles_applied: int


class TopSellingProduct(BaseModel):
    product_name: str
    variant_name: Optional[str] = None
    sku: str
    quantity_sold: int
    revenue_ngn: Decimal


class InventoryReport(BaseModel):
    total_variants: int
    total_stock_on_hand: int
    total_reserved: int
    total_available: int
    low_stock_count: int
    out_of_stock_count: int
    total_stock_value_ngn: Decimal


class SupplierPerformanceItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    supplier_id: uuid.UUID
    supplier_name: str
    total_products: int
    total_orders: int
    total_revenue_ngn: Decimal
    total_payouts_ngn: Decimal
    commission_percent: Decimal


class ReportsOverview(BaseModel):
    sales: SalesSummary
    top_products: list[TopSellingProduct]
    inventory: InventoryReport


# ============================================================================
# SALES SUMMARY
# ============================================================================


@router.get("/reports/sales", response_model=SalesSummary)
async def get_sales_summary(
    days: int = Query(30, ge=1, le=365, description="Number of days to look back"),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get sales summary for the given period."""
    period_end = datetime.utcnow()
    period_start = period_end - timedelta(days=days)

    # Order aggregates
    order_query = select(
        func.count(Order.id).label("total_orders"),
        func.count(case((Order.status == OrderStatus.PAID, Order.id))).label(
            "paid_orders"
        ),
        func.count(case((Order.status == OrderStatus.CANCELLED, Order.id))).label(
            "cancelled_orders"
        ),
        func.coalesce(
            func.sum(
                case(
                    (Order.status == OrderStatus.PAID, Order.total_ngn),
                    else_=Decimal("0"),
                )
            ),
            Decimal("0"),
        ).label("total_revenue"),
        func.coalesce(
            func.sum(
                case(
                    (Order.status == OrderStatus.PAID, Order.discount_amount_ngn),
                    else_=Decimal("0"),
                )
            ),
            Decimal("0"),
        ).label("total_discount"),
        func.coalesce(
            func.sum(
                case(
                    (Order.status == OrderStatus.PAID, Order.delivery_fee_ngn),
                    else_=Decimal("0"),
                )
            ),
            Decimal("0"),
        ).label("total_delivery_fees"),
        func.coalesce(
            func.sum(
                case((Order.status == OrderStatus.PAID, Order.bubbles_applied), else_=0)
            ),
            0,
        ).label("total_bubbles"),
    ).where(Order.created_at >= period_start)
    result = await db.execute(order_query)
    row = result.one()

    total_orders = row.total_orders or 0
    paid_orders = row.paid_orders or 0
    total_revenue = Decimal(str(row.total_revenue or 0))

    # Items sold count
    items_query = (
        select(func.coalesce(func.sum(OrderItem.quantity), 0))
        .join(Order, OrderItem.order_id == Order.id)
        .where(Order.created_at >= period_start, Order.status == OrderStatus.PAID)
    )
    items_result = await db.execute(items_query)
    total_items = items_result.scalar() or 0

    avg_order = total_revenue / paid_orders if paid_orders > 0 else Decimal("0")

    return SalesSummary(
        period_start=period_start,
        period_end=period_end,
        total_orders=total_orders,
        paid_orders=paid_orders,
        cancelled_orders=row.cancelled_orders or 0,
        total_revenue_ngn=total_revenue,
        total_discount_ngn=Decimal(str(row.total_discount or 0)),
        total_delivery_fees_ngn=Decimal(str(row.total_delivery_fees or 0)),
        average_order_value_ngn=avg_order,
        total_items_sold=total_items,
        total_bubbles_applied=row.total_bubbles or 0,
    )


# ============================================================================
# TOP SELLING PRODUCTS
# ============================================================================


@router.get("/reports/top-products", response_model=list[TopSellingProduct])
async def get_top_selling_products(
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(10, ge=1, le=50),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get top selling products by quantity for the given period."""
    period_start = datetime.utcnow() - timedelta(days=days)

    query = (
        select(
            OrderItem.product_name,
            OrderItem.variant_name,
            OrderItem.sku,
            func.sum(OrderItem.quantity).label("qty_sold"),
            func.sum(OrderItem.line_total_ngn).label("revenue"),
        )
        .join(Order, OrderItem.order_id == Order.id)
        .where(Order.created_at >= period_start, Order.status == OrderStatus.PAID)
        .group_by(OrderItem.product_name, OrderItem.variant_name, OrderItem.sku)
        .order_by(func.sum(OrderItem.quantity).desc())
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()

    return [
        TopSellingProduct(
            product_name=r.product_name,
            variant_name=r.variant_name,
            sku=r.sku,
            quantity_sold=r.qty_sold or 0,
            revenue_ngn=Decimal(str(r.revenue or 0)),
        )
        for r in rows
    ]


# ============================================================================
# INVENTORY REPORT
# ============================================================================


@router.get("/reports/inventory", response_model=InventoryReport)
async def get_inventory_report(
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get inventory overview report."""
    query = select(
        func.count(InventoryItem.id).label("total_variants"),
        func.coalesce(func.sum(InventoryItem.quantity_on_hand), 0).label(
            "total_on_hand"
        ),
        func.coalesce(func.sum(InventoryItem.quantity_reserved), 0).label(
            "total_reserved"
        ),
        func.count(
            case(
                (
                    InventoryItem.quantity_on_hand <= InventoryItem.low_stock_threshold,
                    InventoryItem.id,
                )
            )
        ).label("low_stock"),
        func.count(case((InventoryItem.quantity_on_hand == 0, InventoryItem.id))).label(
            "out_of_stock"
        ),
    )
    result = await db.execute(query)
    row = result.one()

    total_on_hand = row.total_on_hand or 0
    total_reserved = row.total_reserved or 0

    # Stock value estimate (sum of on_hand × variant price)
    value_query = select(
        func.coalesce(
            func.sum(
                InventoryItem.quantity_on_hand
                * func.coalesce(
                    ProductVariant.price_override_ngn,
                    cast(0, Float),
                )
            ),
            0,
        )
    ).join(ProductVariant, InventoryItem.variant_id == ProductVariant.id)
    value_result = await db.execute(value_query)
    stock_value = Decimal(str(value_result.scalar() or 0))

    return InventoryReport(
        total_variants=row.total_variants or 0,
        total_stock_on_hand=total_on_hand,
        total_reserved=total_reserved,
        total_available=total_on_hand - total_reserved,
        low_stock_count=row.low_stock or 0,
        out_of_stock_count=row.out_of_stock or 0,
        total_stock_value_ngn=stock_value,
    )


# ============================================================================
# SUPPLIER PERFORMANCE
# ============================================================================


@router.get("/reports/suppliers", response_model=list[SupplierPerformanceItem])
async def get_supplier_performance(
    days: int = Query(30, ge=1, le=365),
    current_user: AuthUser = Depends(require_admin),
    db: AsyncSession = Depends(get_async_db),
):
    """Get supplier performance report for the given period."""
    period_start = datetime.utcnow() - timedelta(days=days)

    # Supplier details with product count
    suppliers_query = (
        select(Supplier)
        .where(Supplier.is_active.is_(True))
        .options(selectinload(Supplier.products))
    )
    suppliers_result = await db.execute(suppliers_query)
    suppliers = suppliers_result.scalars().all()

    performance = []
    for supplier in suppliers:
        # Orders containing this supplier's products
        order_stats_query = (
            select(
                func.count(func.distinct(OrderItem.order_id)).label("order_count"),
                func.coalesce(func.sum(OrderItem.line_total_ngn), Decimal("0")).label(
                    "revenue"
                ),
            )
            .join(Order, OrderItem.order_id == Order.id)
            .where(
                OrderItem.supplier_id == supplier.id,
                Order.status == OrderStatus.PAID,
                Order.created_at >= period_start,
            )
        )
        stats_result = await db.execute(order_stats_query)
        stats = stats_result.one()

        # Total payouts
        payout_query = select(
            func.coalesce(func.sum(SupplierPayout.payout_amount_ngn), Decimal("0"))
        ).where(
            SupplierPayout.supplier_id == supplier.id,
            SupplierPayout.status == "paid",
        )
        payout_result = await db.execute(payout_query)
        total_payouts = payout_result.scalar() or Decimal("0")

        performance.append(
            SupplierPerformanceItem(
                supplier_id=supplier.id,
                supplier_name=supplier.name,
                total_products=len(supplier.products) if supplier.products else 0,
                total_orders=stats.order_count or 0,
                total_revenue_ngn=Decimal(str(stats.revenue or 0)),
                total_payouts_ngn=Decimal(str(total_payouts)),
                commission_percent=supplier.commission_percent or Decimal("0"),
            )
        )

    return performance
