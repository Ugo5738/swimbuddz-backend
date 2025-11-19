import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from mcp.swimbuddz_core_mcp import tools

# Mock token
MOCK_TOKEN = "test-token"

@pytest.mark.asyncio
async def test_get_current_member_profile():
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "123", "email": "test@example.com"}
        mock_request.return_value = mock_response

        result = await tools.get_current_member_profile(MOCK_TOKEN)
        
        assert result["email"] == "test@example.com"
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        assert args[0] == "GET"
        assert "/api/v1/members/me" in args[1]
        assert kwargs["headers"]["Authorization"] == f"Bearer {MOCK_TOKEN}"

@pytest.mark.asyncio
async def test_sign_in_to_session():
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "signed_in"}
        mock_request.return_value = mock_response

        result = await tools.sign_in_to_session(MOCK_TOKEN, "session-123", needs_ride=True)
        
        assert result["status"] == "signed_in"
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        assert args[0] == "POST"
        assert "/api/v1/attendance/sessions/session-123/sign-in" in args[1]
        assert kwargs["json"]["needs_ride"] is True

@pytest.mark.asyncio
async def test_create_announcement():
    with patch("httpx.AsyncClient.request", new_callable=AsyncMock) as mock_request:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "ann-1", "title": "Test"}
        mock_request.return_value = mock_response

        result = await tools.create_announcement(MOCK_TOKEN, "Test", "Body", "general")
        
        assert result["id"] == "ann-1"
        mock_request.assert_called_once()
        args, kwargs = mock_request.call_args
        assert args[0] == "POST"
        assert "/api/v1/communications/announcements/" in args[1]
        assert kwargs["json"]["title"] == "Test"
