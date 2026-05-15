"""Shared helpers + constants for the volunteer member router package."""

from services.volunteer_service.models import VolunteerOpportunity

# QR check-in time window constants (minutes)
_QR_CHECKIN_BEFORE_MINUTES = 15
_QR_CHECKIN_AFTER_MINUTES = 30


async def _enrich_opportunity(opp: VolunteerOpportunity) -> dict:
    """Add role_title and role_category enrichment."""
    data = {c.key: getattr(opp, c.key) for c in opp.__table__.columns}
    data["role_title"] = opp.role.title if opp.role else None
    data["role_category"] = opp.role.category.value if opp.role else None
    # Strip QR token from member-facing responses (volunteers should not see raw token)
    data["qr_token"] = None
    return data
