"""HTTP clients for gateway to call microservices."""
import httpx
from typing import Any, Dict, List, Optional
from libs.common.config import get_settings

settings = get_settings()


class ServiceClient:
    """Base client for making HTTP requests to microservices."""
    
    def __init__(self, base_url: str, timeout: float = 10.0):
        self.base_url = base_url
        self.timeout = timeout
    
    async def get(self, path: str, headers: Optional[Dict] = None) -> Any:
        """Make GET request to service."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{self.base_url}{path}", headers=headers or {})
            response.raise_for_status()
            return response.json()
    
    async def post(self, path: str, json: Dict, headers: Optional[Dict] = None) -> Any:
        """Make POST request to service."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.base_url}{path}", 
                json=json, 
                headers=headers or {}
            )
            response.raise_for_status()
            return response.json()
    
    async def patch(self, path: str, json: Dict, headers: Optional[Dict] = None) -> Any:
        """Make PATCH request to service."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.patch(
                f"{self.base_url}{path}", 
                json=json, 
                headers=headers or {}
            )
            response.raise_for_status()
            return response.json()
    
    async def delete(self, path: str, headers: Optional[Dict] = None) -> Any:
        """Make DELETE request to service."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.delete(f"{self.base_url}{path}", headers=headers or {})
            response.raise_for_status()
            return response.json()


# Service client instances
members_client = ServiceClient(settings.MEMBERS_SERVICE_URL)
sessions_client = ServiceClient(settings.SESSIONS_SERVICE_URL)
attendance_client = ServiceClient(settings.ATTENDANCE_SERVICE_URL)
communications_client = ServiceClient(settings.COMMUNICATIONS_SERVICE_URL)
payments_client = ServiceClient(settings.PAYMENTS_SERVICE_URL)
academy_client = ServiceClient(settings.ACADEMY_SERVICE_URL)
