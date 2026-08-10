from services.communications_service.routers.digest import article_frontend_path


def test_public_digest_articles_use_shareable_route():
    assert article_frontend_path("community") == "/tips"


def test_paid_tier_digest_articles_use_protected_route():
    assert article_frontend_path("club") == "/community/tips"
    assert article_frontend_path("academy") == "/community/tips"
    assert article_frontend_path("unexpected") == "/community/tips"
