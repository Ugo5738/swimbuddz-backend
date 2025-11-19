import httpx
from typing import Any, Dict, List, Optional
from libs.common.config import get_settings

settings = get_settings()

async def _make_request(method: str, path: str, token: str, json: Optional[Dict] = None) -> Any:
    """Helper to make authenticated requests to the Gateway."""
    url = f"{settings.GATEWAY_URL}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    
    async with httpx.AsyncClient() as client:
        response = await client.request(method, url, headers=headers, json=json)
        response.raise_for_status()
        return response.json()

async def get_current_member_profile(token: str) -> Dict[str, Any]:
    """Get the profile of the currently authenticated member."""
    return await _make_request("GET", "/api/v1/members/me", token)

async def list_upcoming_sessions(token: str) -> List[Dict[str, Any]]:
    """List all upcoming sessions."""
    return await _make_request("GET", "/api/v1/sessions/", token)

async def get_session_details(token: str, session_id: str) -> Dict[str, Any]:
    """Get details of a specific session."""
    return await _make_request("GET", f"/api/v1/sessions/{session_id}", token)

async def sign_in_to_session(token: str, session_id: str, needs_ride: bool = False, can_offer_ride: bool = False, ride_notes: Optional[str] = None) -> Dict[str, Any]:
    """Sign in to a session."""
    payload = {
        "needs_ride": needs_ride,
        "can_offer_ride": can_offer_ride,
        "ride_notes": ride_notes
    }
    return await _make_request("POST", f"/api/v1/attendance/sessions/{session_id}/sign-in", token, json=payload)

async def get_my_attendance_history(token: str) -> List[Dict[str, Any]]:
    """Get the attendance history of the current member."""
    return await _make_request("GET", "/api/v1/attendance/me/attendance", token)

async def list_announcements(token: str) -> List[Dict[str, Any]]:
    """List all announcements."""
    return await _make_request("GET", "/api/v1/communications/announcements/", token)

async def create_announcement(token: str, title: str, body: str, category: str, is_pinned: bool = False) -> Dict[str, Any]:
    """Create a new announcement (Admin only)."""
    payload = {
        "title": title,
        "body": body,
        "category": category,
        "is_pinned": is_pinned
    }
    return await _make_request("POST", "/api/v1/communications/announcements/", token, json=payload)
