"""Enum definitions for store service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class ProductType(str, enum.Enum):
    STANDARD = "standard"
    BUNDLE = "bundle"


class ProductStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SourcingType(str, enum.Enum):
    STOCKED = "stocked"
    PREORDER = "preorder"
    DROPSHIP = "dropship"
    CONSIGNMENT = "consignment"


class InventoryMovementType(str, enum.Enum):
    RESTOCK = "restock"
    SALE = "sale"
    RESERVATION = "reservation"
    RELEASE = "release"
    ADJUSTMENT = "adjustment"
    RETURN = "return"


class CartStatus(str, enum.Enum):
    ACTIVE = "active"
    CONVERTED = "converted"
    ABANDONED = "abandoned"
    EXPIRED = "expired"


class OrderStatus(str, enum.Enum):
    PENDING_PAYMENT = "pending_payment"
    PAID = "paid"
    PROCESSING = "processing"
    READY_FOR_PICKUP = "ready_for_pickup"
    PICKED_UP = "picked_up"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    PAYMENT_FAILED = "payment_failed"


class FulfillmentType(str, enum.Enum):
    PICKUP = "pickup"
    DELIVERY = "delivery"


class StoreCreditSourceType(str, enum.Enum):
    RETURN = "return"
    GOODWILL = "goodwill"
    PROMOTION = "promotion"
    ADMIN = "admin"


class SupplierStatus(str, enum.Enum):
    ACTIVE = "active"
    PROBATION = "probation"
    SUSPENDED = "suspended"
    INACTIVE = "inactive"


class PayoutStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAID = "paid"
    FAILED = "failed"


class AuditEntityType(str, enum.Enum):
    PRODUCT = "product"
    INVENTORY = "inventory"
    ORDER = "order"
    ORDER_CASE = "order_case"
    STORE_CREDIT = "store_credit"
    CATEGORY = "category"
    PICKUP_LOCATION = "pickup_location"
    SUPPLIER = "supplier"
    SUPPLIER_PAYOUT = "supplier_payout"


class OrderPaymentStatus(str, enum.Enum):
    """Payment dimension of a store order, independent of fulfillment (ADR-023)."""

    UNPAID = "unpaid"
    PENDING_REVIEW = "pending_review"
    PAID = "paid"
    PARTIALLY_REFUNDED = "partially_refunded"
    REFUNDED = "refunded"
    FAILED = "failed"


class OrderFulfillmentStatus(str, enum.Enum):
    """Physical fulfillment dimension of a store order (ADR-023)."""

    NOT_STARTED = "not_started"
    AWAITING_PREPARATION = "awaiting_preparation"
    PROCESSING = "processing"
    READY_FOR_PICKUP = "ready_for_pickup"
    PICKED_UP = "picked_up"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    RETURNED = "returned"


class OrderCaseType(str, enum.Enum):
    """Kind of post-purchase customer request or remedy (ADR-023)."""

    CANCEL_REQUEST = "cancel_request"
    VARIANT_SWAP = "variant_swap"
    RETURN_EXCHANGE = "return_exchange"
    DEFECT_CLAIM = "defect_claim"


class OrderCaseStatus(str, enum.Enum):
    """Lifecycle of a post-purchase order case."""

    SUBMITTED = "submitted"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    DECLINED = "declined"
    AWAITING_RETURN = "awaiting_return"
    RETURN_RECEIVED = "return_received"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class OrderCaseResolution(str, enum.Enum):
    """Approved or requested remedy for a post-purchase case."""

    FULL_REFUND_ORIGINAL_METHOD = "full_refund_original_method"
    PARTIAL_REFUND_ORIGINAL_METHOD = "partial_refund_original_method"
    VARIANT_EXCHANGE = "variant_exchange"
    STORE_CREDIT = "store_credit"
    WALLET_BUBBLES = "wallet_bubbles"
    REPLACEMENT_SHIPMENT = "replacement_shipment"
    NO_REFUND = "no_refund"


class ReturnInspectionCondition(str, enum.Enum):
    """Physical condition recorded when a returned item is received."""

    PENDING = "pending"
    RESTOCKABLE = "restockable"
    DAMAGED = "damaged"
    HYGIENE_UNSELLABLE = "hygiene_unsellable"
