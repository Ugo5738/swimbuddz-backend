"""FastAPI application entrypoint for the SwimBuddz gateway service.

This gateway now proxies requests to independent microservices instead of
importing them directly.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import httpx

from services.gateway_service.app import clients


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="SwimBuddz Gateway Service",
        version="0.1.0",
        description="API Gateway that orchestrates SwimBuddz microservices.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["system"])
    async def health_check() -> dict[str, str]:
        """Simple readiness endpoint."""
        return {"status": "ok"}

    # ==================================================================
    # MEMBERS SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/members/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_members(path: str, request: Request):
        """Proxy all /api/v1/members/* requests to members service."""
        return await proxy_request(clients.members_client, f"/members/{path}", request)

    @app.api_route(
        "/api/v1/pending-registrations", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    @app.api_route(
        "/api/v1/pending-registrations/", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_pending_registrations_root(request: Request):
        """Proxy pending registration root requests to members service."""
        return await proxy_request(
            clients.members_client, "/pending-registrations/", request
        )

    @app.api_route(
        "/api/v1/pending-registrations/{path:path}",
        methods=["GET", "POST", "PATCH", "DELETE"],
    )
    async def proxy_pending_registrations(path: str, request: Request):
        """Proxy pending registration requests to members service."""
        return await proxy_request(
            clients.members_client, f"/pending-registrations/{path}", request
        )

    # ==================================================================
    # SESSIONS SERVICE PROXY
    # ==================================================================
    # Handle sessions root endpoint (both with and without trailing slash)
    @app.api_route("/api/v1/sessions", methods=["GET", "POST"])
    @app.api_route("/api/v1/sessions/", methods=["GET", "POST"])
    async def proxy_sessions_root(request: Request):
        """Proxy sessions list and create requests to sessions service."""
        return await proxy_request(clients.sessions_client, "/sessions/", request)

    @app.api_route("/api/v1/sessions/{session_id}/attendance", methods=["GET"])
    async def proxy_session_attendance(session_id: str, request: Request):
        """Proxy session attendance requests to attendance service."""
        return await proxy_request(
            clients.attendance_client,
            f"/attendance/sessions/{session_id}/attendance",
            request,
        )

    @app.api_route("/api/v1/sessions/{session_id}/pool-list", methods=["GET"])
    async def proxy_session_pool_list(session_id: str, request: Request):
        """Proxy session pool list requests to attendance service."""
        return await proxy_request(
            clients.attendance_client,
            f"/attendance/sessions/{session_id}/pool-list",
            request,
        )

    @app.api_route(
        "/api/v1/sessions/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_sessions(path: str, request: Request):
        """Proxy all /api/v1/sessions/* requests to sessions service."""
        return await proxy_request(
            clients.sessions_client, f"/sessions/{path}", request
        )

    # ==================================================================
    # VOLUNTEER & CHALLENGE PROXY (Members Service)
    # ==================================================================
    @app.api_route(
        "/api/v1/volunteers/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_volunteers(path: str, request: Request):
        """Proxy all /api/v1/volunteers/* requests to members service."""
        return await proxy_request(
            clients.members_client, f"/volunteers/{path}", request
        )

    @app.api_route(
        "/api/v1/challenges/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_challenges(path: str, request: Request):
        """Proxy all /api/v1/challenges/* requests to members service."""
        return await proxy_request(
            clients.members_client, f"/challenges/{path}", request
        )

    # ==================================================================
    # ATTENDANCE SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/attendance/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_attendance(path: str, request: Request):
        """Proxy all /api/v1/attendance/* requests to attendance service."""
        return await proxy_request(
            clients.attendance_client, f"/attendance/{path}", request
        )

    # ==================================================================
    # COMMUNICATIONS SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/communications/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_communications(path: str, request: Request):
        """Proxy all /api/v1/communications/* requests to communications service."""
        # Communications router has prefix="/announcements"
        # Gateway path is "announcements" (from /api/v1/communications/announcements)
        # We want to call /announcements
        # So we should forward /{path}
        return await proxy_request(clients.communications_client, f"/{path}", request)

    @app.api_route(
        "/api/v1/content/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_content(path: str, request: Request):
        """Proxy all /api/v1/content/* requests to communications service."""
        return await proxy_request(
            clients.communications_client, f"/content/{path}", request
        )

    # ==================================================================
    # PAYMENTS SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/payments/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_payments(path: str, request: Request):
        """Proxy all /api/v1/payments/* requests to payments service."""
        return await proxy_request(
            clients.payments_client, f"/payments/{path}", request
        )

    # ==================================================================
    # ACADEMY SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/academy/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_academy(path: str, request: Request):
        """Proxy all /api/v1/academy/* requests to academy service."""
        return await proxy_request(clients.academy_client, f"/academy/{path}", request)

    # ==================================================================
    # MEDIA SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/media/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_media(path: str, request: Request):
        """Proxy all /api/v1/media/* requests to media service."""
        return await proxy_request(clients.media_client, f"/media/{path}", request)

    # ==================================================================
    # TRANSPORT SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/transport/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_transport(path: str, request: Request):
        """Proxy all /api/v1/transport/* requests to transport service."""
        return await proxy_request(
            clients.transport_client, f"/transport/{path}", request
        )

    # ==================================================================
    # EVENTS SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/events/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_events(path: str, request: Request):
        """Proxy all /api/v1/events/* requests to events service."""
        return await proxy_request(clients.events_client, f"/events/{path}", request)

    # ==================================================================
    # DASHBOARD (Gateway-specific aggregation)
    # ==================================================================
    from services.gateway_service.app.routers.dashboard import (
        router as dashboard_router,
    )

    app.include_router(dashboard_router, prefix="/api/v1")

    return app


async def proxy_request(client: clients.ServiceClient, path: str, request: Request):
    """Generic proxy function to forward requests to microservices."""
    try:
        # Get request body if present
        body = None
        if request.method in ["POST", "PATCH", "PUT"]:
            body = await request.json() if await request.body() else {}

        # Forward headers (excluding those that httpx handles)
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ["content-length", "host"]
        }

        # Make request to service
        if request.method == "GET":
            result = await client.get(path, headers=headers)
        elif request.method == "POST":
            result = await client.post(path, json=body, headers=headers)
        elif request.method == "PUT":
            result = await client.put(path, json=body, headers=headers)
        elif request.method == "PATCH":
            result = await client.patch(path, json=body, headers=headers)
        elif request.method == "DELETE":
            result = await client.delete(path, headers=headers)
        else:
            raise HTTPException(status_code=405, detail="Method not allowed")

        # Handle 204 No Content responses (result will be None)
        if result is None:
            from fastapi.responses import Response

            return Response(status_code=204)

        return JSONResponse(content=result)

    except httpx.HTTPStatusError as e:
        # Forward HTTP errors from services
        try:
            error_content = e.response.json()
        except Exception:
            error_content = {"detail": e.response.text or str(e)}

        return JSONResponse(status_code=e.response.status_code, content=error_content)
    except httpx.RequestError as e:
        # Handle connection errors
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")


app = create_app()
