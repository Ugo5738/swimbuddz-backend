"""Canonical enums shared across services.

Cross-service contract enums live here so each service can re-export from
its own ``models.enums`` module without duplicating the value list. This
keeps payment-flow strings consistent when payments_service writes a row
and another service later reads it via HTTP.

Important: even after a service re-exports an enum from here, its DB enum
type stays scoped to that service's schema. The Python class and the DB
type can drift if a value is added here and the affected services' DB
enum types aren't migrated to match. Today this is intentional for
``PaymentStatus.PENDING_REVIEW`` — only payments_service writes it, so
academy_service's ``academy_payment_status_enum`` doesn't carry it.
"""

from __future__ import annotations

import enum


class PaymentStatus(str, enum.Enum):
    """Lifecycle state of a payment record.

    Used by payments_service (full set) and academy_service (uses PENDING /
    PAID / WAIVED / FAILED only — never writes PENDING_REVIEW today).
    """

    PENDING = "pending"
    PENDING_REVIEW = "pending_review"
    PAID = "paid"
    WAIVED = "waived"
    FAILED = "failed"


__all__ = ["PaymentStatus"]
