"""Batch recipient prices without querying other services' tables."""

from libs.common.config import get_settings
from libs.common.logging import get_logger
from libs.common.service_client import internal_post

logger = get_logger(__name__)


async def load_member_session_prices(
    sessions: list[dict], members: list[dict]
) -> dict[str, dict[str, dict]]:
    checks = [
        {
            "context_key": f"{session['id']}:{member_id}",
            "session_id": str(session["id"]),
            "member_id": member_id,
        }
        for session in sessions
        if str(session.get("session_type") or "").lower() == "club"
        for member_id in dict.fromkeys(
            str(member["id"]) for member in members if member.get("id")
        )
    ]
    prices: dict[str, dict[str, dict]] = {}
    for offset in range(0, len(checks), 500):
        batch = checks[offset : offset + 500]
        try:
            response = await internal_post(
                service_url=get_settings().SESSIONS_SERVICE_URL,
                path="/internal/sessions/member-prices",
                calling_service="communications",
                json={"checks": batch},
            )
            response.raise_for_status()
            by_key = {item["context_key"]: item for item in response.json()["items"]}
            for check in batch:
                if check["context_key"] in by_key:
                    prices.setdefault(check["session_id"], {})[check["member_id"]] = (
                        by_key[check["context_key"]]
                    )
        except Exception as exc:
            # Do not substitute a public fee when the member's quote cannot be
            # verified. The email keeps its useful swim details and booking link.
            logger.warning(
                "Recipient session prices unavailable: %s", type(exc).__name__
            )
    return prices
