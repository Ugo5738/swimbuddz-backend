"""GET /payments/pricing — public read-only fee schedule.

No auth, no DB write; just exposes the configured membership prices
so the frontend can render the tier cards without a round-trip.
"""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from libs.auth.dependencies import _service_role_jwt, get_current_user, require_admin
from libs.auth.models import AuthUser
from libs.common.config import get_settings
from libs.common.currency import KOBO_PER_NAIRA
from libs.common.emails.client import get_email_client
from libs.common.logging import get_logger
from libs.common.service_client import (
    dispatch_notification,
    emit_rewards_event,
    get_member_by_auth_id,
    internal_post,
)
from libs.db.session import get_async_db
from services.payments_service.models import (
    Discount,
    DiscountType,
    Payment,
    PaymentPurpose,
    PaymentStatus,
)
from services.payments_service.schemas import (
    ClubBillingCycle,
    CompletePaymentRequest,
    CreatePaymentIntentRequest,
    PaymentIntentResponse,
    PaymentResponse,
    PricingConfigResponse,
    SessionAttendanceRole,
    SessionAttendanceStatus,
)

settings = get_settings()
logger = get_logger(__name__)

FULFILLMENT_META_KEY = "fulfillment"
MAX_FULFILLMENT_RETRIES = 8
BASE_FULFILLMENT_RETRY_MINUTES = 2

router = APIRouter()


@router.get("/pricing", response_model=PricingConfigResponse)
async def get_pricing_config():
    """
    Get public pricing configuration for membership tiers.
    No authentication required - used by frontend to display prices.
    """
    return PricingConfigResponse(
        community_annual=settings.COMMUNITY_ANNUAL_FEE_NGN,
        club_quarterly=settings.CLUB_QUARTERLY_FEE_NGN,
        club_biannual=settings.CLUB_BIANNUAL_FEE_NGN,
        club_annual=settings.CLUB_ANNUAL_FEE_NGN,
        currency="NGN",
    )
