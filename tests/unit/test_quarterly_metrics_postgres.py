"""Optional local PostgreSQL checks; never connect to production."""

import importlib
import json
import os
import uuid
from datetime import datetime, time

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Enum, create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from services.communications_service.models import ContentPost
from services.communications_service.routers.quarterly_metrics import (
    get_reporting_summary as content_summary,
)
from services.members_service.models import (
    Club,
    ClubEnrollment,
    ClubPlanVersion,
    Pod,
    PodAssignment,
    CommunityExperienceOrder,
    CommunityExperiencePurchase,
    CommunityExperienceParticipant,
    CommunityExperienceAttendance,
)
from services.members_service.models.enums import DayOfWeek, PodAssignmentSource
from services.members_service.routers.quarterly_metrics import (
    get_reporting_summary as membership_summary,
)
from services.reporting_service.services.quarter_utils import quarter_date_range
from services.reporting_service.models import MemberQuarterlyReport, LeaderboardCategory
from services.reporting_service.routers.community_reports import get_leaderboard
from libs.auth.models import AuthUser

TEST_URL = os.environ.get("QUARTERLY_VALIDATION_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not TEST_URL, reason="Requires an isolated local PostgreSQL validation database"
)


@pytest.fixture
def isolated_schema():
    assert (
        "@127.0.0.1:" in TEST_URL or "@localhost:" in TEST_URL
    ), "Local validation database only"
    schema = "quarterly_test_" + uuid.uuid4().hex
    engine = create_engine(TEST_URL)
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(text(f'SET search_path TO "{schema}"'))
        tables = [
            model.__table__
            for model in [
                ContentPost,
                Club,
                ClubEnrollment,
                ClubPlanVersion,
                Pod,
                PodAssignment,
                CommunityExperienceOrder,
                CommunityExperiencePurchase,
                CommunityExperienceParticipant,
                CommunityExperienceAttendance,
            ]
        ]
        for table in tables:
            for column in table.columns:
                if isinstance(column.type, Enum):
                    column.type.create(conn, checkfirst=True)
            # Service soft-reference IDs need no external service fixture.
            conn.execute(CreateTable(table, include_foreign_key_constraints=[]))
    yield engine, schema
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    engine.dispose()


def test_nullable_jsonb_migrations_preserve_legacy_rows_and_round_trip(isolated_schema):
    engine, schema = isolated_schema
    reporting = importlib.import_module(
        "services.reporting_service.alembic.versions.d4e8f1a6b209_expand_quarterly_activity"
    )
    content = importlib.import_module(
        "services.communications_service.alembic.versions.e2f6a9c1d308_add_episode_air_date"
    )
    with engine.begin() as conn:
        conn.execute(text(f'SET search_path TO "{schema}"'))
        for table in ["member_quarterly_reports", "community_quarterly_stats"]:
            conn.execute(text(f"CREATE TABLE {table} (id integer primary key)"))
            conn.execute(text(f"INSERT INTO {table} (id) VALUES (1)"))
        context = MigrationContext.configure(conn)
        with Operations.context(context):
            reporting.upgrade()
            assert (
                conn.execute(
                    text("SELECT activity_details FROM member_quarterly_reports")
                ).scalar_one()
                is None
            )
            conn.execute(
                text(
                    "UPDATE member_quarterly_reports SET activity_details = CAST(:payload AS jsonb)"
                ),
                {"payload": json.dumps({"attendance": {"all_sessions_attended": 7}})},
            )
            assert (
                conn.execute(
                    text("SELECT activity_details FROM member_quarterly_reports")
                ).scalar_one()["attendance"]["all_sessions_attended"]
                == 7
            )
            reporting.downgrade()
            assert (
                conn.execute(
                    text("SELECT id FROM member_quarterly_reports")
                ).scalar_one()
                == 1
            )
            content.downgrade()
            content.upgrade()
            reporting.upgrade()


@pytest.mark.asyncio
async def test_original_broadcast_date_keeps_q3_episode_out_of_q4_upload_count(
    isolated_schema,
):
    _, schema = isolated_schema
    engine = create_async_engine(
        TEST_URL, connect_args={"options": f"-csearch_path={schema}"}
    )
    async with async_sessionmaker(engine)() as db:
        db.add_all(
            [
                ContentPost(
                    title="Q3 broadcast, October upload",
                    body="test",
                    category="beyond_the_pool",
                    episode_number=1,
                    episode_air_date=datetime(2026, 8, 6).date(),
                    published_at=datetime.fromisoformat("2026-10-03T00:00:00+01:00"),
                    is_published=True,
                    created_by=uuid.uuid4(),
                ),
                ContentPost(
                    title="October 1 broadcast",
                    body="test",
                    category="beyond_the_pool",
                    episode_number=3,
                    episode_air_date=datetime(2026, 10, 1).date(),
                    published_at=datetime.fromisoformat("2026-10-01T00:00:00+01:00"),
                    is_published=True,
                    created_by=uuid.uuid4(),
                ),
                ContentPost(
                    title="Last fractional second article",
                    body="test",
                    category="safety",
                    published_at=datetime.fromisoformat(
                        "2026-09-30T23:59:59.999999+01:00"
                    ),
                    is_published=True,
                    created_by=uuid.uuid4(),
                ),
            ]
        )
        await db.commit()
        q3 = await content_summary(*quarter_date_range(2026, 3), None, db)
        q4 = await content_summary(*quarter_date_range(2026, 4), None, db)
        assert q3.metrics["podcast_episodes_published"] == 1
        assert q3.metrics["content_posts_published"] == 1
        assert q3.details["episodes"][0]["episode_number"] == 1
        assert q4.metrics["podcast_episodes_published"] == 1
        assert q4.details["episodes"][0]["episode_number"] == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_configured_and_populated_pods_use_historical_membership_dates(
    isolated_schema,
):
    _, schema = isolated_schema
    engine = create_async_engine(
        TEST_URL, connect_args={"options": f"-csearch_path={schema}"}
    )
    async with async_sessionmaker(engine)() as db:
        club_id, lead = uuid.uuid4(), uuid.uuid4()
        db.add(
            Club(
                id=club_id,
                name="Yaba",
                slug="yaba",
                created_at=datetime.fromisoformat("2026-05-01T00:00:00+01:00"),
            )
        )
        pod_ids = [uuid.uuid4() for _ in range(5)]
        for i, pod_id in enumerate(pod_ids):
            db.add(
                Pod(
                    id=pod_id,
                    club_id=club_id,
                    name=f"Pod {i}",
                    slug=f"pod-{i}",
                    pod_lead_id=lead,
                    created_by=lead,
                    default_session_day=DayOfWeek.SAT,
                    default_session_time=time(9),
                    created_at=datetime.fromisoformat(
                        "2026-10-01T00:00:00+01:00"
                        if i == 3
                        else "2026-07-10T00:00:00+01:00"
                    ),
                    review_due_at=datetime.fromisoformat("2026-10-31T00:00:00+01:00"),
                    dissolved_at=datetime.fromisoformat("2026-09-01T00:00:00+01:00")
                    if i == 4
                    else None,
                )
            )
        for i in [0, 1, 4]:
            db.add(
                PodAssignment(
                    pod_id=pod_ids[i],
                    member_id=uuid.uuid4(),
                    assigned_by=PodAssignmentSource.ADMIN,
                    joined_at=datetime.fromisoformat("2026-07-15T00:00:00+01:00"),
                    left_at=datetime.fromisoformat("2026-09-01T00:00:00+01:00")
                    if i == 1
                    else None,
                )
            )
        await db.commit()
        result = await membership_summary(*quarter_date_range(2026, 3), None, None, db)
        assert result.metrics["clubs_configured"] == 1
        assert result.metrics["pods_configured"] == 3
        assert result.metrics["pods_with_assignments"] == 3
        assert result.metrics["pods_populated_at_end"] == 1
        assert result.metrics["pod_members_at_end"] == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_rewards_leaderboard_excludes_legacy_credits_and_private_members(
    isolated_schema,
):
    sync_engine, schema = isolated_schema
    with sync_engine.begin() as conn:
        conn.execute(text(f'SET search_path TO "{schema}"'))
        conn.execute(
            CreateTable(
                MemberQuarterlyReport.__table__, include_foreign_key_constraints=[]
            )
        )
    engine = create_async_engine(
        TEST_URL, connect_args={"options": f"-csearch_path={schema}"}
    )
    async with async_sessionmaker(engine)() as db:
        for auth, credits, rewards, private in [
            ("a", 420, 5, False),
            ("b", 10, 8, False),
            ("legacy", 999, None, False),
            ("private", 999, 999, True),
        ]:
            db.add(
                MemberQuarterlyReport(
                    member_id=uuid.uuid4(),
                    member_auth_id=auth,
                    year=2026,
                    quarter=3,
                    member_name=auth,
                    total_sessions_attended=3,
                    bubbles_earned=credits,
                    leaderboard_opt_out=private,
                    activity_details={
                        "wallet": {
                            "available": True,
                            "metrics": {"reward_bubbles": rewards},
                        }
                    }
                    if rewards is not None
                    else None,
                )
            )
        await db.commit()
        result = await get_leaderboard(
            2026, 3, LeaderboardCategory.BUBBLES_EARNED, 20, AuthUser(sub="a"), db
        )
        assert [(e.member_name, e.value) for e in result.entries] == [
            ("b", 8),
            ("a", 5),
        ]
        assert result.current_user_eligible is True
    await engine.dispose()
