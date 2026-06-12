"""FastAPI application entrypoint for the SwimBuddz gateway service.

This gateway now proxies requests to independent microservices instead of
importing them directly.
"""

from __future__ import annotations

import re

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from slowapi.errors import RateLimitExceeded

from libs.common.error_handler import add_exception_handlers
from libs.common.health import register_health_check
from libs.common.logging import get_request_id
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
        # With allow_credentials=True, wildcard methods/headers materially broaden
        # the attack surface (any allowed origin can fire any custom-headered
        # request). Enumerate the verbs and headers we actually use.
        allow_methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
    )

    # Add observability (structured logging + request tracing)
    add_observability_middleware(app)

    # Add global exception handlers for consistent error responses
    add_exception_handlers(app)

    register_health_check(app, "gateway")

    # ==================================================================
    # MEMBERS SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/members/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_members(path: str, request: Request):
        """Proxy all /api/v1/members/* requests to members service."""
        return await proxy_request(clients.members_client, f"/members/{path}", request)

    @app.api_route(
        "/api/v1/admin/members/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_members(path: str, request: Request):
        """Proxy all /api/v1/admin/members/* requests to members service."""
        return await proxy_request(
            clients.members_client, f"/admin/members/{path}", request
        )

    @app.api_route(
        "/api/v1/pending-registrations",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    @app.api_route(
        "/api/v1/pending-registrations/",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    @limiter.limit("5/minute")
    async def proxy_pending_registrations_root(request: Request):
        """Proxy pending registration root requests to members service."""
        return await proxy_request(
            clients.members_client, "/pending-registrations/", request
        )

    @app.api_route(
        "/api/v1/pending-registrations/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    @limiter.limit("5/minute")
    async def proxy_pending_registrations(path: str, request: Request):
        """Proxy pending registration requests to members service."""
        return await proxy_request(
            clients.members_client, f"/pending-registrations/{path}", request
        )

    # ==================================================================
    # ASSESSMENTS PROXY (routed to members service)
    # ==================================================================
    # Reads are unrestricted; submissions are rate-limited because the
    # assessment POST accepts anonymous users (see _optional_member_id in
    # members_service/routers/assessments.py) and is a marketing-funnel
    # endpoint that would otherwise be trivial to spam.
    @app.api_route("/api/v1/assessments", methods=["GET"])
    @app.api_route("/api/v1/assessments/", methods=["GET"])
    async def proxy_assessments_root_get(request: Request):
        """Proxy assessment list/read requests to members service."""
        return await proxy_request(clients.members_client, "/assessments/", request)

    @app.api_route("/api/v1/assessments", methods=["POST"])
    @app.api_route("/api/v1/assessments/", methods=["POST"])
    @limiter.limit("5/minute")
    async def proxy_assessments_root_post(request: Request):
        """Proxy assessment submissions to members service (rate-limited)."""
        return await proxy_request(clients.members_client, "/assessments/", request)

    @app.api_route("/api/v1/assessments/{path:path}", methods=["GET"])
    async def proxy_assessments_get(path: str, request: Request):
        """Proxy assessment sub-resource reads to members service."""
        return await proxy_request(
            clients.members_client, f"/assessments/{path}", request
        )

    @app.api_route("/api/v1/assessments/{path:path}", methods=["POST"])
    @limiter.limit("5/minute")
    async def proxy_assessments_post(path: str, request: Request):
        """Proxy assessment sub-resource writes to members service (rate-limited)."""
        return await proxy_request(
            clients.members_client, f"/assessments/{path}", request
        )

    # ==================================================================
    # COACHES SERVICE PROXY (routed to members service)
    # ==================================================================
    @app.api_route(
        "/api/v1/coaches/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_coaches(path: str, request: Request):
        """Proxy all /api/v1/coaches/* requests to members service (coach router)."""
        return await proxy_request(clients.members_client, f"/coaches/{path}", request)

    @app.api_route(
        "/api/v1/admin/coaches/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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

    # A1 Phase 3.3 — booking is rate-limited because the Bubbles fast path
    # debits the wallet inside POST /sessions/{id}/book. Per-user/IP cap
    # (libs.common.rate_limit keys by user when authed) stops a runaway
    # client from hammering wallet debits. Reads/other session writes go
    # through the catch-all below unthrottled. Registered BEFORE the
    # catch-all so this specific (method, path) wins.
    @app.api_route("/api/v1/sessions/{session_id}/book", methods=["POST"])
    @limiter.limit("10/minute")
    async def proxy_session_book(session_id: str, request: Request):
        """Proxy session pre-booking to sessions service (rate-limited)."""
        return await proxy_request(
            clients.sessions_client, f"/sessions/{session_id}/book", request
        )

    @app.api_route(
        "/api/v1/sessions/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_sessions(path: str, request: Request):
        """Proxy all /api/v1/sessions/* requests to sessions service."""
        return await proxy_request(
            clients.sessions_client, f"/sessions/{path}", request
        )

    @app.api_route(
        "/api/v1/makeups/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_makeups(path: str, request: Request):
        """Proxy all /api/v1/makeups/* requests to sessions service (make-up router)."""
        return await proxy_request(clients.sessions_client, f"/makeups/{path}", request)

    @app.api_route(
        "/api/v1/admin/sessions/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_sessions(path: str, request: Request):
        """Proxy all /api/v1/admin/sessions/* requests to sessions service.
        (Pods admin moved to `/admin/members/pods/*` in May 2026 — handled
        by the existing /api/v1/admin/members/* wildcard proxy above.)"""
        return await proxy_request(
            clients.sessions_client, f"/admin/sessions/{path}", request
        )

    # ==================================================================
    # VOLUNTEER SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/volunteers/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_volunteers(path: str, request: Request):
        """Proxy all /api/v1/volunteers/* requests to volunteer service."""
        return await proxy_request(
            clients.volunteer_client, f"/volunteers/{path}", request
        )

    @app.api_route(
        "/api/v1/admin/volunteers/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_volunteers(path: str, request: Request):
        """Proxy all /api/v1/admin/volunteers/* requests to volunteer service."""
        return await proxy_request(
            clients.volunteer_client, f"/admin/volunteers/{path}", request
        )

    # ==================================================================
    # CHALLENGE PROXY (Members Service)
    # ==================================================================
    @app.api_route(
        "/api/v1/challenges/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_challenges(path: str, request: Request):
        """Proxy all /api/v1/challenges/* requests to members service."""
        return await proxy_request(
            clients.members_client, f"/challenges/{path}", request
        )

    # ==================================================================
    # CLUBS PROXY (Members Service)
    # ==================================================================
    @app.api_route("/api/v1/clubs", methods=["GET", "POST"])
    @app.api_route("/api/v1/clubs/", methods=["GET", "POST"])
    async def proxy_clubs_root(request: Request):
        """Proxy /api/v1/clubs and /api/v1/clubs/ to members service."""
        return await proxy_request(clients.members_client, "/clubs/", request)

    @app.api_route(
        "/api/v1/clubs/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_clubs(path: str, request: Request):
        """Proxy all /api/v1/clubs/* requests to members service."""
        return await proxy_request(clients.members_client, f"/clubs/{path}", request)

    # ==================================================================
    # ATTENDANCE SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/attendance/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_attendance(path: str, request: Request):
        """Proxy all /api/v1/attendance/* requests to attendance service."""
        return await proxy_request(
            clients.attendance_client, f"/attendance/{path}", request
        )

    @app.api_route("/api/v1/cohorts/{cohort_id}/attendance/summary", methods=["GET"])
    async def proxy_cohort_attendance_summary(cohort_id: str, request: Request):
        """Proxy cohort attendance summary to attendance service."""
        return await proxy_request(
            clients.attendance_client,
            f"/attendance/cohorts/{cohort_id}/attendance/summary",
            request,
        )

    # ==================================================================
    # COMMUNICATIONS SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/communications/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_communications(path: str, request: Request):
        """Proxy all /api/v1/communications/* requests to communications service."""
        # Communications router has prefix="/announcements"
        # Gateway path is "announcements" (from /api/v1/communications/announcements)
        # We want to call /announcements
        # So we should forward /{path}
        return await proxy_request(clients.communications_client, f"/{path}", request)

    @app.api_route(
        "/api/v1/content/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_content(path: str, request: Request):
        """Proxy all /api/v1/content/* requests to communications service."""
        return await proxy_request(
            clients.communications_client, f"/content/{path}", request
        )

    @app.api_route("/api/v1/messages/{path:path}", methods=["GET", "POST"])
    async def proxy_messages(path: str, request: Request):
        """Proxy all /api/v1/messages/* requests to communications service."""
        return await proxy_request(
            clients.communications_client, f"/messages/{path}", request
        )

    @app.api_route("/api/v1/email/{path:path}", methods=["POST"])
    async def proxy_email(path: str, request: Request):
        """Proxy all /api/v1/email/* requests to communications service (internal)."""
        return await proxy_request(
            clients.communications_client, f"/email/{path}", request
        )

    @app.api_route("/api/v1/preferences/{path:path}", methods=["GET", "PATCH"])
    async def proxy_preferences(path: str, request: Request):
        """Proxy all /api/v1/preferences/* requests to communications service.

        POST was previously allowed for the public /preferences/check-opt-in
        endpoint, which has been removed (no auth, no callers — see
        communications_service/routers/preferences.py). Only the
        member-facing GET/PATCH /preferences/me remain.
        """
        return await proxy_request(
            clients.communications_client, f"/preferences/{path}", request
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
        "/api/v1/payments/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
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
    # media_service routers now live under a service-local `/media` prefix
    # (matching every other service); the gateway adds the public
    # `/api/v1/` namespace. The bare path maps to the list/create endpoint
    # at `@router.post("/media")` / `@router.get("/media")`, which is why
    # the proxy target is `/media/media`.
    @app.api_route("/api/v1/media", methods=["GET", "POST"])
    @app.api_route("/api/v1/media/", methods=["GET", "POST"])
    async def proxy_media_root(request: Request):
        """Proxy media root requests (list and upload) to media service."""
        return await proxy_request(clients.media_client, "/media/media", request)

    @app.api_route(
        "/api/v1/media/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"],
    )
    async def proxy_media(path: str, request: Request):
        """Proxy all /api/v1/media/* requests to media service."""
        if _MEDIA_PLAYBACK_PATH.fullmatch(path) and request.method in ("GET", "HEAD"):
            return await proxy_media_playback(path, request)
        if request.method == "HEAD":
            # Only playback supports HEAD; everything else keeps the
            # generic proxy's GET/POST/PUT/PATCH/DELETE surface.
            raise HTTPException(status_code=405, detail="Method not allowed")
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
        "/api/v1/events/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_events(path: str, request: Request):
        """Proxy all /api/v1/events/* requests to events service."""
        return await proxy_request(clients.events_client, f"/events/{path}", request)

    # ==================================================================
    # STORE SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/store/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_store(path: str, request: Request):
        """Proxy all /api/v1/store/* requests to store service."""
        return await proxy_request(clients.store_client, f"/store/{path}", request)

    @app.api_route(
        "/api/v1/admin/store/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_store(path: str, request: Request):
        """Proxy all /api/v1/admin/store/* requests to store service."""
        return await proxy_request(
            clients.store_client, f"/admin/store/{path}", request
        )

    # ==================================================================
    # WALLET SERVICE PROXY
    # ==================================================================
    @app.api_route(
        "/api/v1/wallet/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_wallet(path: str, request: Request):
        """Proxy all /api/v1/wallet/* requests to wallet service."""
        return await proxy_request(clients.wallet_client, f"/wallet/{path}", request)

    @app.api_route(
        "/api/v1/admin/wallet/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_wallet(path: str, request: Request):
        """Proxy all /api/v1/admin/wallet/* requests to wallet service."""
        return await proxy_request(
            clients.wallet_client, f"/admin/wallet/{path}", request
        )

    # ==================================================================
    # POOLS SERVICE PROXY
    # ==================================================================
    # Exact-match routes for bare paths avoid a 307 redirect chain:
    #   1. Frontend hits /api/v1/admin/pools (no trailing slash)
    #   2. Without an exact route, FastAPI's redirect_slashes=True would 307 →
    #      /api/v1/admin/pools/, the proxy forwards /admin/pools/, pools-service
    #      then 307s back to /admin/pools (no slash), and the browser follows
    #      the Location relative to the current origin → lands on the admin HTML
    #      page instead of the API. Net result: "Failed to load pools".
    # Handling the bare path explicitly short-circuits all of that.
    @app.api_route("/api/v1/pools", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def proxy_pools_root(request: Request):
        return await proxy_request(clients.pools_client, "/pools", request)

    @app.api_route(
        "/api/v1/pools/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_pools(path: str, request: Request):
        """Proxy all /api/v1/pools/* requests to pools service."""
        target = f"/pools/{path}" if path else "/pools"
        return await proxy_request(clients.pools_client, target, request)

    @app.api_route(
        "/api/v1/admin/pools",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_pools_root(request: Request):
        return await proxy_request(clients.pools_client, "/admin/pools", request)

    @app.api_route(
        "/api/v1/admin/pools/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_pools(path: str, request: Request):
        """Proxy all /api/v1/admin/pools/* requests to pools service."""
        target = f"/admin/pools/{path}" if path else "/admin/pools"
        return await proxy_request(clients.pools_client, target, request)

    # ==================================================================
    # AI SERVICE
    # ==================================================================
    @app.api_route(
        "/api/v1/ai/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"]
    )
    async def proxy_ai(path: str, request: Request):
        """Proxy all /api/v1/ai/* requests to AI service."""
        return await proxy_request(clients.ai_client, f"/ai/{path}", request)

    # ==================================================================
    # REPORTING SERVICE
    # ==================================================================
    @app.api_route(
        "/api/v1/reports/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_reports(path: str, request: Request):
        """Proxy all /api/v1/reports/* requests to reporting service."""
        return await proxy_request(
            clients.reporting_client, f"/reports/{path}", request
        )

    @app.api_route(
        "/api/v1/admin/reports/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_reports(path: str, request: Request):
        """Proxy all /api/v1/admin/reports/* requests to reporting service."""
        return await proxy_request(
            clients.reporting_client, f"/admin/reports/{path}", request
        )

    # ==================================================================
    # CHAT SERVICE PROXY (Phase 0 scaffold — Phase 1 endpoints to follow)
    # ==================================================================
    @app.api_route(
        "/api/v1/chat/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_chat(path: str, request: Request):
        """Proxy all /api/v1/chat/* requests to chat service."""
        return await proxy_request(clients.chat_client, f"/chat/{path}", request)

    @app.api_route(
        "/api/v1/admin/chat/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_chat(path: str, request: Request):
        """Proxy all /api/v1/admin/chat/* requests to chat service."""
        return await proxy_request(clients.chat_client, f"/admin/chat/{path}", request)

    # ==================================================================
    # CORPORATE SERVICE PROXY
    # ==================================================================
    # Public path — powers the marketing site's intake form at
    # swimbuddz.com/corporate. Rate limiting + honeypot live in
    # corporate_service itself, not here.
    @app.api_route(
        "/api/v1/corporate/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_corporate(path: str, request: Request):
        """Proxy all /api/v1/corporate/* requests to corporate service."""
        target = f"/corporate/{path}" if path else "/corporate"
        return await proxy_request(clients.corporate_client, target, request)

    # Admin path — full CRUD over the pipeline.
    @app.api_route(
        "/api/v1/admin/corporate",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_corporate_root(request: Request):
        return await proxy_request(
            clients.corporate_client, "/admin/corporate", request
        )

    @app.api_route(
        "/api/v1/admin/corporate/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_corporate(path: str, request: Request):
        """Proxy all /api/v1/admin/corporate/* requests to corporate service."""
        target = f"/admin/corporate/{path}" if path else "/admin/corporate"
        return await proxy_request(clients.corporate_client, target, request)

    # ==================================================================
    # LEDGER SERVICE PROXY
    # ==================================================================
    # Admin / finance surface only. Internal posting (/internal/ledger/*) is
    # service-to-service (direct call, service-role JWT) and intentionally NOT
    # proxied. /api/v1/ledger/* is reserved for future member-facing endpoints
    # (e.g. a member viewing their own invoices) — not implemented in Phase 1.
    @app.api_route(
        "/api/v1/admin/finance/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_finance(path: str, request: Request):
        """Proxy all /api/v1/admin/finance/* requests to ledger service."""
        return await proxy_request(
            clients.ledger_client, f"/admin/finance/{path}", request
        )

    # ==================================================================
    # WEATHER SERVICE PROXY
    # ==================================================================
    # Member/authenticated read surface — cached forecast for coordinates or a
    # specific pool. Served by pools_service (weather module).
    @app.api_route(
        "/api/v1/weather/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_weather(path: str, request: Request):
        """Proxy all /api/v1/weather/* requests to pools service (weather module)."""
        target = f"/weather/{path}" if path else "/weather"
        return await proxy_request(clients.pools_client, target, request)

    # Admin surface — manual pre-fetch refresh + snapshot inspection.
    @app.api_route(
        "/api/v1/admin/weather/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def proxy_admin_weather(path: str, request: Request):
        """Proxy all /api/v1/admin/weather/* requests to pools service (weather module)."""
        target = f"/admin/weather/{path}" if path else "/admin/weather"
        return await proxy_request(clients.pools_client, target, request)

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


# Matches the service-local playback path, e.g. "media/{uuid}/play".
_MEDIA_PLAYBACK_PATH = re.compile(r"media/[0-9a-fA-F-]+/play")

# Like _filter_service_headers but keeps Content-Length/Content-Type: the
# relayed responses are bodyless (a 307 or a HEAD answer), so the browser
# needs the media service's own values, not ones recomputed from the body.
_PLAYBACK_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-encoding",
    "host",
}


async def proxy_media_playback(path: str, request: Request):
    """Relay /media/{id}/play to the browser without consuming the redirect.

    The media service answers playback GETs with a 307 to a freshly presigned
    S3 URL so the *browser* streams video bytes straight from S3 (and gets a
    fresh URL on every Range request). The generic proxy follows redirects,
    which made the gateway download and buffer whole videos in memory instead
    — large evidence videos stalled and failed on slow connections. Browser
    HEAD probes must likewise reach the media service's HEAD handler rather
    than 405 at the gateway.
    """
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ["content-length", "host"]
    }
    if not any(k.lower() == "x-request-id" for k in headers):
        request_id = get_request_id()
        if request_id:
            headers["X-Request-ID"] = request_id
    headers.setdefault("X-Caller-Service", "gateway")

    target = f"/media/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    try:
        if request.method == "HEAD":
            service_response = await clients.media_client.head(target, headers=headers)
        else:
            service_response = await clients.media_client.get(
                target, headers=headers, follow_redirects=False
            )
    except httpx.HTTPStatusError as e:
        try:
            error_content = e.response.json()
        except Exception:
            error_content = {"detail": e.response.text or str(e)}
        return JSONResponse(status_code=e.response.status_code, content=error_content)
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Service unavailable: {str(e)}")

    forward_headers = {
        k: v
        for k, v in service_response.headers.items()
        if k.lower() not in _PLAYBACK_HOP_BY_HOP
    }
    return Response(
        status_code=service_response.status_code,
        headers=forward_headers,
    )


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
        if not any(k.lower() == "x-request-id" for k in headers):
            request_id = get_request_id()
            if request_id:
                headers["X-Request-ID"] = request_id
        headers.setdefault("X-Caller-Service", "gateway")

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
