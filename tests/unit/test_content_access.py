import uuid

from fastapi.routing import APIRoute

from libs.auth.dependencies import get_current_user, get_optional_user, require_admin
from services.communications_service.routers.content import content_router
from services.communications_service.services.content_access import (
    ContentActor,
    allowed_content_tiers,
    can_read_content,
)
from tests.factories import ContentPostFactory


def actor(*tiers: str, admin: bool = False) -> ContentActor:
    return ContentActor(
        member_id=uuid.uuid4() if tiers else None,
        paid_tiers=frozenset(tiers),
        is_admin=admin,
        is_authenticated=bool(tiers) or admin,
    )


def test_guest_only_receives_community_tier():
    assert allowed_content_tiers(actor()) == {"community"}


def test_unpublished_content_is_hidden_from_members():
    post = ContentPostFactory.create(is_published=False, tier_access="community")
    assert can_read_content(post, actor("community")) is False
    assert can_read_content(post, actor(admin=True)) is True


def test_tier_content_uses_paid_hierarchy_from_backend():
    club_post = ContentPostFactory.create(tier_access="club")
    academy_post = ContentPostFactory.create(tier_access="academy")

    assert can_read_content(club_post, actor("community")) is False
    assert can_read_content(club_post, actor("community", "club")) is True
    assert can_read_content(academy_post, actor("community", "club")) is False
    assert can_read_content(academy_post, actor("community", "club", "academy")) is True


def test_unknown_database_tier_fails_closed():
    post = ContentPostFactory.create(tier_access="unexpected")
    assert can_read_content(post, actor("community", "club", "academy")) is False


def _route_dependencies(route_name: str) -> set:
    route = next(
        route
        for route in content_router.routes
        if isinstance(route, APIRoute) and route.name == route_name
    )
    return {dependency.call for dependency in route.dependant.dependencies}


def test_content_admin_mutations_require_admin_dependency():
    admin_routes = {
        "create_ai_content_draft",
        "create_content_post",
        "update_content_post",
        "publish_content_post",
        "unpublish_content_post",
        "retry_failed_content_post_emails",
        "delete_content_post",
    }

    for route_name in admin_routes:
        assert require_admin in _route_dependencies(route_name)


def test_public_content_reads_resolve_optional_identity():
    assert get_optional_user in _route_dependencies("list_content_posts")
    assert get_optional_user in _route_dependencies("get_content_post")
    assert get_optional_user in _route_dependencies("list_content_comments")
    assert get_current_user in _route_dependencies("create_content_comment")
