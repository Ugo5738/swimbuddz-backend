"""Exercise additive DDL and destructive-downgrade guards in an isolated schema."""

import importlib
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text


@pytest.mark.asyncio
@pytest.mark.integration
async def test_checkout_migrations_preserve_existing_rows_and_guard_live_holds(
    test_engine,
):
    admission = importlib.import_module(
        "services.sessions_service.alembic.versions.82f070b47973_add_template_admission_settings"
    )
    seats = importlib.import_module(
        "services.sessions_service.alembic.versions.e30550028159_add_club_session_capacity_holds"
    )
    plans = importlib.import_module(
        "services.members_service.alembic.versions.1cbbd94c744f_protect_externally_payable_club_"
    )
    schema = f"checkout_migration_{uuid4().hex}"
    async with test_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            await connection.execute(
                text("CREATE TABLE sessions (id uuid PRIMARY KEY)")
            )
            await connection.execute(
                text("CREATE TABLE session_templates (id integer PRIMARY KEY)")
            )
            await connection.execute(text("INSERT INTO session_templates VALUES (1)"))
            await connection.execute(
                text(
                    "CREATE TABLE club_enrollment_reservations (id integer PRIMARY KEY, status varchar(24) NOT NULL, CONSTRAINT ck_club_enrollment_reservation_status CHECK (status IN ('active', 'consumed', 'released')))"
                )
            )
            await connection.execute(
                text("INSERT INTO club_enrollment_reservations VALUES (1, 'active')")
            )

            def migrate(sync, migration, direction):
                with Operations.context(MigrationContext.configure(sync)):
                    getattr(migration, direction)()

            for migration in (admission, seats, plans):
                await connection.run_sync(migrate, migration, "upgrade")
            assert (
                await connection.scalar(
                    text("SELECT admission_settings FROM session_templates WHERE id=1")
                )
                == {}
            )
            assert (
                await connection.scalar(
                    text("SELECT status FROM club_enrollment_reservations WHERE id=1")
                )
                == "active"
            )
            await connection.execute(
                text("UPDATE club_enrollment_reservations SET status='protected'")
            )
            with pytest.raises(RuntimeError, match="protected"):
                await connection.run_sync(migrate, plans, "downgrade")
            sid = str(uuid4())
            await connection.execute(
                text("INSERT INTO sessions VALUES (:sid)"), {"sid": sid}
            )
            await connection.execute(
                text(
                    "INSERT INTO club_session_holds (id,session_id,application_id,plan_version_id,club_id,member_id,payment_reference,status,expires_at,created_at,updated_at) VALUES (:sid,:sid,:sid,:sid,:sid,:sid,'test','protected',now()-interval '1 day',now(),now())"
                ),
                {"sid": sid},
            )
            with pytest.raises(RuntimeError, match="Reconcile live"):
                await connection.run_sync(migrate, seats, "downgrade")
            await connection.execute(
                text("UPDATE club_session_holds SET status='released'")
            )
            await connection.execute(
                text("UPDATE club_enrollment_reservations SET status='released'")
            )
            for migration in (plans, seats, admission):
                await connection.run_sync(migrate, migration, "downgrade")
            assert (
                await connection.scalar(text("SELECT count(*) FROM session_templates"))
                == 1
            )
            for migration in (admission, seats, plans):
                await connection.run_sync(migrate, migration, "upgrade")
        finally:
            await transaction.rollback()  # Includes the isolated schema itself.
