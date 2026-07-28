"""Regression tests for the shared SQLAlchemy declarative registry."""

import subprocess
import sys
import textwrap


def test_all_service_models_configure_together():
    """All service models must coexist in the shared SQLAlchemy registry."""

    code = textwrap.dedent(
        """
        from sqlalchemy.orm import configure_mappers

        modules = [
            "services.academy_service.models",
            "services.attendance_service.models",
            "services.chat_service.models",
            "services.communications_service.models",
            "services.corporate_service.models",
            "services.events_service.models",
            "services.ledger_service.models",
            "services.media_service.models",
            "services.members_service.models",
            "services.payments_service.models",
            "services.pools_service.models",
            "services.reporting_service.models",
            "services.sessions_service.models",
            "services.store_service.models",
            "services.transport_service.models",
            "services.volunteer_service.models",
            "services.wallet_service.models",
        ]

        for module in modules:
            __import__(module)

        configure_mappers()
        """
    )

    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
    )
