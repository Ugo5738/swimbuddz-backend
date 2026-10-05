"""The monthly spotlight's wallet reward, shared by manual and scheduled selection."""

import uuid
from datetime import date

from libs.common.logging import get_logger
from libs.common.service_client import emit_rewards_event, get_member_by_id

logger = get_logger(__name__)


async def award_volunteer_of_month(member_id: uuid.UUID, award_month: date) -> bool:
    """Emit once per member/display month; retries use the same wallet event key."""
    month = award_month.replace(day=1)
    try:
        member = await get_member_by_id(str(member_id), calling_service="volunteer")
        if not member or not member.get("auth_id"):
            logger.warning(
                "Monthly volunteer reward: missing auth identity for %s", member_id
            )
            return False
        result = await emit_rewards_event(
            event_type="volunteer.monthly_spotlight",
            member_auth_id=str(member["auth_id"]),
            member_id=str(member_id),
            service_source="volunteer",
            event_data={"month": month.strftime("%B %Y")},
            idempotency_key=f"volunteer-month:{month:%Y-%m}:{member_id}",
            calling_service="volunteer",
        )
        return bool(
            result and result.get("accepted") and result.get("rewards_granted", 0) > 0
        )
    except Exception:
        logger.exception("Monthly volunteer reward failed for %s", member_id)
        return False
