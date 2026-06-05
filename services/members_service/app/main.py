"""FastAPI application for the Members Service."""

from fastapi import FastAPI

from libs.common.health import register_health_check
from services.members_service.routers import (
    admin_router,
    admin_tasks_router,
    assessments_router,
    challenge_router,
    clubs_router,
    coach_agreements_admin_router,
    coach_agreements_router,
    coach_application_admin_router,
    coach_application_router,
    coach_availability_router,
    coach_banking_router,
    coach_grades_admin_router,
    coach_grades_router,
    coaches_router,
    guardians_admin_router,
    guardians_internal_router,
    internal_pods_router,
    internal_router,
    members_router,
    pods_admin_router,
    pods_member_router,
    registration_router,
    volunteer_router,
)


def create_app() -> FastAPI:
    """Create and configure the Members Service FastAPI app."""
    app = FastAPI(
        title="SwimBuddz Members Service",
        version="0.1.0",
        description="Member management service for SwimBuddz.",
    )

    register_health_check(app, "members")

    # Include routers
    app.include_router(assessments_router)  # Public swim readiness assessment
    app.include_router(coaches_router)  # Public coaches listing endpoints
    app.include_router(members_router)
    app.include_router(registration_router)  # Registration flow endpoints
    app.include_router(admin_router)  # Admin approval endpoints
    app.include_router(admin_tasks_router)  # Admin-triggered manual tasks
    # NOTE: volunteer_router removed — now handled by volunteer_service (port 8012)
    app.include_router(challenge_router)
    app.include_router(clubs_router)
    app.include_router(volunteer_router)

    # Coach routers (profile management, not public listing)
    app.include_router(coach_application_router)
    app.include_router(coach_application_admin_router)
    app.include_router(coach_banking_router)
    app.include_router(coach_availability_router)  # Coach availability editor (Phase 0)
    app.include_router(coach_grades_router)
    app.include_router(coach_grades_admin_router)
    app.include_router(coach_agreements_router)
    app.include_router(coach_agreements_admin_router)

    # Guardian-link endpoints — admin CRUD + internal read for chat_service
    app.include_router(guardians_admin_router)
    app.include_router(guardians_internal_router)

    # Club pods — admin management + member self-selection
    app.include_router(pods_admin_router)
    app.include_router(pods_member_router)

    # Internal service-to-service endpoints (not exposed via gateway).
    # internal_pods_router MUST register BEFORE internal_router — otherwise
    # the broader `/internal/members/{member_id}` route in internal_router
    # eats requests to `/internal/members/pods*` as `member_id="pods"`,
    # which then 422s on UUID validation.
    app.include_router(internal_pods_router)
    app.include_router(internal_router)

    return app


app = create_app()
