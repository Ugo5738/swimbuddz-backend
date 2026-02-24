"""Enum definitions for store service models."""

import enum


def enum_values(enum_cls):
    """Return persistent DB values for SAEnum mappings."""
    return [member.value for member in enum_cls]


class ProductStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class SourcingType(str, enum.Enum):
    STOCKED = "stocked"
    PREORDER = "preorder"


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


class AuditEntityType(str, enum.Enum):
    PRODUCT = "product"
    INVENTORY = "inventory"
    ORDER = "order"
    STORE_CREDIT = "store_credit"
    CATEGORY = "category"
    PICKUP_LOCATION = "pickup_location"
