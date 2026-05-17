"""GET /payments/pricing — public read-only fee schedule.

No auth, no DB write; just exposes the configured membership prices
so the frontend can render the tier cards without a round-trip.
"""

from fastapi import APIRouter

from libs.common.config import get_settings
from libs.common.logging import get_logger
from services.payments_service.schemas import (
    PricingConfigResponse,
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
