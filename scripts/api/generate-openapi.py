"""
Generate a combined OpenAPI schema from all SwimBuddz services.

This script creates a single OpenAPI document that includes all schemas
from individual services, which can then be used to generate TypeScript types.

Each service is wrapped in a try/except so a single broken schema generator
doesn't fail the whole run — instead it logs a warning to stderr and skips
that service's paths/components.
"""

import json
import sys

# Import all service apps to get their OpenAPI schemas
from services.academy_service.app.main import app as academy_app
from services.ai_service.app.main import app as ai_app
from services.attendance_service.app.main import app as attendance_app
from services.chat_service.app.main import app as chat_app
from services.communications_service.app.main import app as comms_app
from services.events_service.app.main import app as events_app
from services.media_service.app.main import app as media_app
from services.members_service.app.main import app as members_app
from services.payments_service.app.main import app as payments_app
from services.pools_service.app.main import app as pools_app
from services.reporting_service.app.main import app as reporting_app
from services.sessions_service.app.main import app as sessions_app
from services.store_service.app.main import app as store_app
from services.transport_service.app.main import app as transport_app
from services.volunteer_service.app.main import app as volunteer_app
from services.wallet_service.app.main import app as wallet_app


def merge_openapi_schemas():
    """Merge OpenAPI schemas from all services into one document."""

    # Base schema structure
    combined = {
        "openapi": "3.1.0",
        "info": {
            "title": "SwimBuddz API",
            "description": "Combined API schema for all SwimBuddz services.",
            "version": "1.0.0",
        },
        "paths": {},
        "components": {"schemas": {}},
    }

    services = [
        ("members", members_app),
        ("sessions", sessions_app),
        ("attendance", attendance_app),
        ("academy", academy_app),
        ("payments", payments_app),
        ("communications", comms_app),
        ("reporting", reporting_app),
        ("wallet", wallet_app),
        ("transport", transport_app),
        ("ai", ai_app),
        ("volunteer", volunteer_app),
        ("pools", pools_app),
        ("store", store_app),
        ("events", events_app),
        ("media", media_app),
        ("chat", chat_app),
    ]

    for prefix, app in services:
        try:
            schema = app.openapi()

            # Merge paths with service prefix
            for path, operations in schema.get("paths", {}).items():
                # Add /api/v1 prefix to match gateway patterns
                full_path = f"/api/v1{path}"
                combined["paths"][full_path] = operations

            # Merge component schemas
            for name, definition in (
                schema.get("components", {}).get("schemas", {}).items()
            ):
                if name not in combined["components"]["schemas"]:
                    combined["components"]["schemas"][name] = definition

        except Exception as e:
            print(f"Warning: Could not process {prefix}: {e}", file=sys.stderr)

    return combined


if __name__ == "__main__":
    schema = merge_openapi_schemas()
    print(json.dumps(schema, indent=2))
