"""FastAPI application entrypoint for the SwimBuddz gateway service.

This gateway now proxies requests to independent microservices instead of
importing them directly.
"""

from __future__ import annotations

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi.errors import RateLimitExceeded

from libs.common.error_handler import add_exception_handlers
from libs.common.middleware import add_observability_middleware
from libs.common.rate_limit import limiter, rate_limit_exceeded_handler
from services.gateway_service.app import clients


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    app = FastAPI(
        title="SwimBuddz Gateway Service",
        version="0.1.0",
        description="API Gateway that orchestrates SwimBuddz microservices.",
    )

    # Add rate limiter state to app
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "https://swimbuddz.com",
            "https://www.swimbuddz.com",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add observability (structured logging + request tracing)
    add_observability_middleware(app)

    # Add global exception handlers for consistent error responses
    add_exception_handlers(app)

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
        "/api/v1/admin/members/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_admin_members(path: str, request: Request):
        """Proxy all /api/v1/admin/members/* requests to members service."""
        return await proxy_request(
            clients.members_client, f"/admin/members/{path}", request
        )

    @app.api_route(
        "/api/v1/pending-registrations", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    @app.api_route(
        "/api/v1/pending-registrations/", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    @limiter.limit("5/minute")
    async def proxy_pending_registrations_root(request: Request):
        """Proxy pending registration root requests to members service."""
        return await proxy_request(
            clients.members_client, "/pending-registrations/", request
        )

    @app.api_route(
        "/api/v1/pending-registrations/{path:path}",
        methods=["GET", "POST", "PATCH", "DELETE"],
    )
    @limiter.limit("5/minute")
    async def proxy_pending_registrations(path: str, request: Request):
        """Proxy pending registration requests to members service."""
        return await proxy_request(
            clients.members_client, f"/pending-registrations/{path}", request
        )

    # ==================================================================
    # COACHES SERVICE PROXY (routed to members service)
    # ==================================================================
    @app.api_route(
        "/api/v1/coaches/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_coaches(path: str, request: Request):
        """Proxy all /api/v1/coaches/* requests to members service (coach router)."""
        return await proxy_request(clients.members_client, f"/coaches/{path}", request)

    @app.api_route(
        "/api/v1/admin/coaches/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"]
    )
    async def proxy_admin_coaches(path: str, request: Request):
        """Proxy all /api/v1/admin/coaches/* requests to members service (admin coach router)."""
        return await proxy_request(
            clients.members_client, f"/admin/coaches/{path}", request
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
    # Payment intent initiation with strict rate limit
    @app.api_route("/api/v1/payments/intents", methods=["POST"])
    @app.api_route("/api/v1/payments/intents/", methods=["POST"])
    @limiter.limit("3/minute")
    async def proxy_payment_intents(request: Request):
        """Proxy payment intent creation with rate limiting."""
        return await proxy_request(
            clients.payments_client, "/payments/intents", request
        )

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
    # Handle media root endpoint (both with and without trailing slash)
    @app.api_route("/api/v1/media", methods=["GET", "POST"])
    @app.api_route("/api/v1/media/", methods=["GET", "POST"])
    async def proxy_media_root(request: Request):
        """Proxy media root requests (list and upload) to media service."""
        return await proxy_request(clients.media_client, "/api/v1/media/media", request)

    @app.api_route(
        "/api/v1/media/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_media(path: str, request: Request):
        """Proxy all /api/v1/media/* requests to media service."""
        return await proxy_request(
            clients.media_client, f"/api/v1/media/{path}", request
        )

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
    from services.gateway_service.app.routers.cleanup import router as cleanup_router
    from services.gateway_service.app.routers.dashboard import (
        router as dashboard_router,
    )

    app.include_router(dashboard_router, prefix="/api/v1")
    app.include_router(cleanup_router, prefix="/api/v1")

    return app


def _filter_service_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
    """Strip hop-by-hop headers that FastAPI/starlette manages."""
    hop_by_hop = {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "content-length",
        "content-encoding",
        "host",
        "content-type",
    }
    return [(k, v) for k, v in headers.items() if k.lower() not in hop_by_hop]


async def proxy_request(client: clients.ServiceClient, path: str, request: Request):
    """Generic proxy function to forward requests to microservices."""
    try:
        # Get request body.
        #
        # IMPORTANT: forward JSON payloads as raw bytes. Re-serializing JSON changes
        # whitespace/key order and breaks webhook signature verification
        # (e.g. Paystack x-paystack-signature).
        content_body = None

        if request.method in ["POST", "PATCH", "PUT"]:
            body_bytes = await request.body()
            if body_bytes:
                content_body = body_bytes

        # Forward headers (excluding those that httpx handles or that cause issues)
        # Content-Length is handled by httpx based on the body we pass
        # Host is set by httpx
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() not in ["content-length", "host"]
        }

        # Include query parameters
        query_params = request.url.query
        if query_params:
            path = f"{path}?{query_params}"

        # Make request to service
        if request.method == "GET":
            service_response = await client.get(path, headers=headers)
        elif request.method == "POST":
            if content_body is not None:
                service_response = await client.post(
                    path, content=content_body, headers=headers
                )
            else:
                service_response = await client.post(path, headers=headers)
        elif request.method == "PUT":
            if content_body is not None:
                service_response = await client.put(
                    path, content=content_body, headers=headers
                )
            else:
                service_response = await client.put(path, headers=headers)
        elif request.method == "PATCH":
            if content_body is not None:
                service_response = await client.patch(
                    path, content=content_body, headers=headers
                )
            else:
                service_response = await client.patch(path, headers=headers)
        elif request.method == "DELETE":
            service_response = await client.delete(path, headers=headers)
        else:
            raise HTTPException(status_code=405, detail="Method not allowed")

        forward_headers = dict(_filter_service_headers(service_response.headers))

        if service_response.status_code == 204:
            return Response(status_code=204, headers=forward_headers)

        content_type = service_response.headers.get("content-type", "")

        if "application/json" in content_type:
            try:
                payload = service_response.json()
                return JSONResponse(
                    content=payload,
                    status_code=service_response.status_code,
                    headers=forward_headers,
                )
            except ValueError:
                # Fall back to raw bytes if the payload is not valid JSON.
                pass

        return Response(
            content=service_response.content,
            status_code=service_response.status_code,
            media_type=content_type or None,
            headers=forward_headers,
        )

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
