"""Integration tests for the daily birthday celebration cron task.

The task lives in services.communications_service.tasks.birthdays. It pulls
today's birthdays from members_service, emails adult opt-ins, creates
in-app Notification rows, and dispatches a single admin reminder.

We patch the cross-service helpers (get_birthdays_today, get_admin_members,
dispatch_notification) and the email sender so we can assert the orchestration
logic without leaving the test process.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select


def _patch_db(db_session):
    """Patch get_async_db inside the birthday task to yield our test session.

    The task does ``async for db in get_async_db():`` and the loop breaks
    after one iteration, so a one-shot async generator is enough.
    """

    async def _gen():
        # Re-route close so the test_session isn't actually closed.
        original_close = db_session.close

        async def _noop_close():
            return None

        db_session.close = _noop_close
        try:
            yield db_session
        finally:
            db_session.close = original_close

    return patch(
        "services.communications_service.tasks.birthdays.get_async_db",
        side_effect=lambda: _gen(),
    )


def _adult(first="Ada", member_id=None, age=30, email=None):
    return {
        "id": str(member_id or uuid.uuid4()),
        "first_name": first,
        "last_name": "Tester",
        "email": email or f"{first.lower()}@test.com",
        "age": age,
    }


def _admin(first="Bola"):
    return {
        "id": str(uuid.uuid4()),
        "first_name": first,
        "last_name": "Admin",
        "email": f"{first.lower()}@admin.com",
        "roles": ["admin"],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_no_birthdays_short_circuits():
    """When members_service returns no birthdays, no emails or admin notifs go out."""
    from services.communications_service.tasks.birthdays import (
        send_daily_birthday_celebrations,
    )

    with (
        patch(
            "services.communications_service.tasks.birthdays.get_birthdays_today",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "services.communications_service.tasks.birthdays.get_admin_members",
            new_callable=AsyncMock,
        ) as get_admins,
        patch(
            "services.communications_service.tasks.birthdays.send_birthday_email",
            new_callable=AsyncMock,
        ) as send_email,
        patch(
            "services.communications_service.tasks.birthdays.dispatch_notification",
            new_callable=AsyncMock,
        ) as dispatch,
    ):
        await send_daily_birthday_celebrations()

    send_email.assert_not_called()
    dispatch.assert_not_called()
    get_admins.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_adult_with_no_prefs_gets_email_and_in_app_notif(db_session):
    """Adult member, no preferences row → email sent + in-app Notification created."""
    from services.communications_service.models import Notification
    from services.communications_service.tasks.birthdays import (
        send_daily_birthday_celebrations,
    )

    adult = _adult(first="Ada", age=30)
    admin = _admin()

    with (
        _patch_db(db_session),
        patch(
            "services.communications_service.tasks.birthdays.get_birthdays_today",
            new_callable=AsyncMock,
            return_value=[adult],
        ),
        patch(
            "services.communications_service.tasks.birthdays.get_admin_members",
            new_callable=AsyncMock,
            return_value=[admin],
        ),
        patch(
            "services.communications_service.tasks.birthdays.send_birthday_email",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_email,
        patch(
            "services.communications_service.tasks.birthdays.dispatch_notification",
            new_callable=AsyncMock,
        ) as dispatch,
    ):
        await send_daily_birthday_celebrations()

    send_email.assert_awaited_once_with(to_email=adult["email"], member_name="Ada")

    # in-app Notification row exists for the adult
    rows = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.member_id == uuid.UUID(adult["id"])
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == 1
    assert rows[0].type == "birthday"
    assert rows[0].category == "announcements"

    # admin reminder fired
    dispatch.assert_awaited_once()
    kwargs = dispatch.await_args.kwargs
    assert kwargs["type"] == "birthday_admin_reminder"
    assert kwargs["member_ids"] == [admin["id"]]
    assert "Ada" in kwargs["body"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_opted_out_adult_gets_no_email(db_session):
    """email_birthday=False → no email, no in-app notification, but still listed for admin."""
    from services.communications_service.models import (
        Notification,
        NotificationPreferences,
    )
    from services.communications_service.tasks.birthdays import (
        send_daily_birthday_celebrations,
    )

    adult = _adult(first="Chidi", age=42)

    db_session.add(
        NotificationPreferences(
            member_id=uuid.UUID(adult["id"]),
            email_birthday=False,
        )
    )
    await db_session.commit()

    with (
        _patch_db(db_session),
        patch(
            "services.communications_service.tasks.birthdays.get_birthdays_today",
            new_callable=AsyncMock,
            return_value=[adult],
        ),
        patch(
            "services.communications_service.tasks.birthdays.get_admin_members",
            new_callable=AsyncMock,
            return_value=[_admin()],
        ),
        patch(
            "services.communications_service.tasks.birthdays.send_birthday_email",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_email,
        patch(
            "services.communications_service.tasks.birthdays.dispatch_notification",
            new_callable=AsyncMock,
        ) as dispatch,
    ):
        await send_daily_birthday_celebrations()

    send_email.assert_not_called()

    # No Notification row for opted-out adult
    rows = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.member_id == uuid.UUID(adult["id"])
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []

    # Admin still gets a reminder, and Chidi is in the body
    dispatch.assert_awaited_once()
    assert "Chidi" in dispatch.await_args.kwargs["body"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_minor_does_not_get_email_but_appears_in_admin_reminder(db_session):
    """Minors (<18) are skipped for email but listed for the admin's WhatsApp shoutout."""
    from services.communications_service.tasks.birthdays import (
        send_daily_birthday_celebrations,
    )

    minor = _adult(first="Zara", age=10)
    adult = _adult(first="Ada", age=30)

    with (
        _patch_db(db_session),
        patch(
            "services.communications_service.tasks.birthdays.get_birthdays_today",
            new_callable=AsyncMock,
            return_value=[minor, adult],
        ),
        patch(
            "services.communications_service.tasks.birthdays.get_admin_members",
            new_callable=AsyncMock,
            return_value=[_admin()],
        ),
        patch(
            "services.communications_service.tasks.birthdays.send_birthday_email",
            new_callable=AsyncMock,
            return_value=True,
        ) as send_email,
        patch(
            "services.communications_service.tasks.birthdays.dispatch_notification",
            new_callable=AsyncMock,
        ) as dispatch,
    ):
        await send_daily_birthday_celebrations()

    # Only the adult got an email
    assert send_email.await_count == 1
    assert send_email.await_args.kwargs["member_name"] == "Ada"

    # Admin reminder lists BOTH names
    dispatch.assert_awaited_once()
    body = dispatch.await_args.kwargs["body"]
    assert "Ada" in body
    assert "Zara" in body
    metadata = dispatch.await_args.kwargs["metadata"]
    assert metadata["birthday_count"] == 2
    assert metadata["celebrated_member_ids"] == [adult["id"]]
    assert set(metadata["all_birthday_member_ids"]) == {minor["id"], adult["id"]}


@pytest.mark.asyncio
@pytest.mark.integration
async def test_admin_title_pluralisation(db_session):
    """Title says '1 birthday' for one match and '<n> birthdays' for many."""
    from services.communications_service.tasks.birthdays import (
        send_daily_birthday_celebrations,
    )

    one = [_adult(first="Solo", age=25)]
    many = [
        _adult(first="One", age=25),
        _adult(first="Two", age=27),
        _adult(first="Three", age=30),
    ]

    for members, expected in [(one, "1 birthday today"), (many, "3 birthdays today")]:
        with (
            _patch_db(db_session),
            patch(
                "services.communications_service.tasks.birthdays.get_birthdays_today",
                new_callable=AsyncMock,
                return_value=members,
            ),
            patch(
                "services.communications_service.tasks.birthdays.get_admin_members",
                new_callable=AsyncMock,
                return_value=[_admin()],
            ),
            patch(
                "services.communications_service.tasks.birthdays.send_birthday_email",
                new_callable=AsyncMock,
                return_value=True,
            ),
            patch(
                "services.communications_service.tasks.birthdays.dispatch_notification",
                new_callable=AsyncMock,
            ) as dispatch,
        ):
            await send_daily_birthday_celebrations()

        title = dispatch.await_args.kwargs["title"]
        assert expected in title


@pytest.mark.asyncio
@pytest.mark.integration
async def test_email_failure_does_not_block_admin_reminder(db_session):
    """If the email sender raises, the admin still gets the WhatsApp reminder."""
    from services.communications_service.models import Notification
    from services.communications_service.tasks.birthdays import (
        send_daily_birthday_celebrations,
    )

    adult = _adult(first="Tunde", age=33)

    with (
        _patch_db(db_session),
        patch(
            "services.communications_service.tasks.birthdays.get_birthdays_today",
            new_callable=AsyncMock,
            return_value=[adult],
        ),
        patch(
            "services.communications_service.tasks.birthdays.get_admin_members",
            new_callable=AsyncMock,
            return_value=[_admin()],
        ),
        patch(
            "services.communications_service.tasks.birthdays.send_birthday_email",
            new_callable=AsyncMock,
            side_effect=RuntimeError("SMTP down"),
        ),
        patch(
            "services.communications_service.tasks.birthdays.dispatch_notification",
            new_callable=AsyncMock,
        ) as dispatch,
    ):
        await send_daily_birthday_celebrations()

    # No Notification row created for failed-email adult
    rows = (
        (
            await db_session.execute(
                select(Notification).where(
                    Notification.member_id == uuid.UUID(adult["id"])
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []

    # Admin reminder still fired
    dispatch.assert_awaited_once()
    assert "Tunde" in dispatch.await_args.kwargs["body"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_no_admins_skips_reminder(db_session):
    """If members_service returns no admins, the dispatch is skipped (no recipients)."""
    from services.communications_service.tasks.birthdays import (
        send_daily_birthday_celebrations,
    )

    with (
        _patch_db(db_session),
        patch(
            "services.communications_service.tasks.birthdays.get_birthdays_today",
            new_callable=AsyncMock,
            return_value=[_adult(age=30)],
        ),
        patch(
            "services.communications_service.tasks.birthdays.get_admin_members",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "services.communications_service.tasks.birthdays.send_birthday_email",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "services.communications_service.tasks.birthdays.dispatch_notification",
            new_callable=AsyncMock,
        ) as dispatch,
    ):
        await send_daily_birthday_celebrations()

    dispatch.assert_not_called()
