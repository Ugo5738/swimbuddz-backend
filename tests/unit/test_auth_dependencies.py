"""Unit tests for libs/auth role-check dependencies.

Security-critical code that had zero dedicated tests (review finding E3).
These exercise the pure authorization logic — no DB, no HTTP, no JWT
decode. `validate_token`/`get_current_user` (JWT/JWKS) are integration
concerns covered elsewhere; here we test the role matrix that gates
every admin/coach/safeguarding route.

The require_* functions take an already-resolved AuthUser (FastAPI
resolves it via get_current_user) and raise HTTPException(403) on
failure, else return the user.
"""

import pytest
from fastapi import HTTPException

from libs.auth import dependencies as deps
from libs.auth.dependencies import (
    is_admin_or_service,
    require_admin,
    require_coach,
    require_safeguarding_admin,
    require_service_role,
)
from libs.auth.models import AuthUser


def _user(*, role="authenticated", roles=None, email=None) -> AuthUser:
    app_metadata = {} if roles is None else {"roles": roles}
    return AuthUser(
        sub="00000000-0000-0000-0000-000000000001",
        email=email,
        role=role,
        app_metadata=app_metadata,
    )


# ---------------------------------------------------------------------------
# AuthUser.roles / has_role
# ---------------------------------------------------------------------------


class TestAuthUserRoles:
    def test_roles_from_app_metadata_list(self):
        u = _user(roles=["admin", "coach"])
        assert u.roles == ["admin", "coach"]
        assert u.has_role("admin")
        assert u.has_role("coach")
        assert not u.has_role("safeguarding_admin")

    def test_roles_from_app_metadata_string(self):
        u = _user(roles="coach")
        assert u.roles == ["coach"]
        assert u.has_role("coach")

    def test_roles_fallback_to_token_role(self):
        u = _user(role="service_role")
        assert u.roles == ["service_role"]
        assert u.has_role("service_role")

    def test_no_roles_no_metadata_falls_back_to_authenticated(self):
        u = _user()
        assert u.roles == ["authenticated"]
        assert not u.has_role("admin")


# ---------------------------------------------------------------------------
# require_admin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRequireAdmin:
    async def test_admin_role_allowed(self):
        u = _user(roles=["admin"])
        assert await require_admin(u) is u

    async def test_service_role_allowed(self):
        u = _user(role="service_role")
        assert await require_admin(u) is u

    async def test_whitelisted_email_allowed(self, monkeypatch):
        monkeypatch.setattr(
            deps.settings, "ADMIN_EMAILS", ["boss@swimbuddz.com"], raising=False
        )
        u = _user(email="boss@swimbuddz.com")
        assert await require_admin(u) is u

    async def test_plain_user_rejected(self):
        u = _user()
        with pytest.raises(HTTPException) as exc:
            await require_admin(u)
        assert exc.value.status_code == 403

    async def test_coach_is_not_admin(self):
        u = _user(roles=["coach"])
        with pytest.raises(HTTPException) as exc:
            await require_admin(u)
        assert exc.value.status_code == 403

    async def test_non_whitelisted_email_rejected(self, monkeypatch):
        monkeypatch.setattr(
            deps.settings, "ADMIN_EMAILS", ["boss@swimbuddz.com"], raising=False
        )
        u = _user(email="random@example.com")
        with pytest.raises(HTTPException):
            await require_admin(u)


# ---------------------------------------------------------------------------
# require_service_role
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRequireServiceRole:
    async def test_service_role_allowed(self):
        u = _user(role="service_role")
        assert await require_service_role(u) is u

    async def test_admin_role_is_not_service_role(self):
        # Admin is NOT service_role — internal endpoints must stay
        # service-role-only even for human admins.
        u = _user(roles=["admin"])
        with pytest.raises(HTTPException) as exc:
            await require_service_role(u)
        assert exc.value.status_code == 403

    async def test_plain_user_rejected(self):
        with pytest.raises(HTTPException):
            await require_service_role(_user())


# ---------------------------------------------------------------------------
# require_coach
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRequireCoach:
    async def test_coach_allowed(self):
        u = _user(roles=["coach"])
        assert await require_coach(u) is u

    async def test_admin_allowed(self):
        u = _user(roles=["admin"])
        assert await require_coach(u) is u

    async def test_service_role_allowed(self):
        u = _user(role="service_role")
        assert await require_coach(u) is u

    async def test_whitelisted_email_allowed(self, monkeypatch):
        monkeypatch.setattr(
            deps.settings, "ADMIN_EMAILS", ["boss@swimbuddz.com"], raising=False
        )
        u = _user(email="boss@swimbuddz.com")
        assert await require_coach(u) is u

    async def test_plain_user_rejected(self):
        with pytest.raises(HTTPException) as exc:
            await require_coach(_user())
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# require_safeguarding_admin — general admin is deliberately NOT enough
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRequireSafeguardingAdmin:
    async def test_safeguarding_admin_allowed(self):
        u = _user(roles=["safeguarding_admin"])
        assert await require_safeguarding_admin(u) is u

    async def test_service_role_allowed(self):
        u = _user(role="service_role")
        assert await require_safeguarding_admin(u) is u

    async def test_general_admin_rejected(self):
        # Key safeguarding rule: general admins are NOT automatically
        # safeguarding admins (CHAT_SERVICE_DESIGN §6).
        u = _user(roles=["admin"])
        with pytest.raises(HTTPException) as exc:
            await require_safeguarding_admin(u)
        assert exc.value.status_code == 403

    async def test_whitelisted_email_rejected(self, monkeypatch):
        # Email whitelist grants admin, NOT safeguarding-admin.
        monkeypatch.setattr(
            deps.settings, "ADMIN_EMAILS", ["boss@swimbuddz.com"], raising=False
        )
        u = _user(email="boss@swimbuddz.com")
        with pytest.raises(HTTPException):
            await require_safeguarding_admin(u)

    async def test_plain_user_rejected(self):
        with pytest.raises(HTTPException):
            await require_safeguarding_admin(_user())


# ---------------------------------------------------------------------------
# is_admin_or_service helper
# ---------------------------------------------------------------------------


class TestIsAdminOrService:
    def test_admin_true(self):
        assert is_admin_or_service(_user(roles=["admin"])) is True

    def test_service_true(self):
        assert is_admin_or_service(_user(role="service_role")) is True

    def test_whitelisted_email_true(self, monkeypatch):
        monkeypatch.setattr(
            deps.settings, "ADMIN_EMAILS", ["boss@swimbuddz.com"], raising=False
        )
        assert is_admin_or_service(_user(email="boss@swimbuddz.com")) is True

    def test_plain_user_false(self):
        assert is_admin_or_service(_user()) is False

    def test_coach_false(self):
        assert is_admin_or_service(_user(roles=["coach"])) is False
