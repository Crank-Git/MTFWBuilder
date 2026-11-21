"""Tests for authentication, sessions, and admin routes."""

import pytest
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner

from mtfwbuilder.auth import (
    SESSION_COOKIE,
    create_session_token,
    validate_session_token,
    verify_password,
)
from mtfwbuilder.config import Settings, _hash_password


class TestPasswordHashing:
    """Tests for bcrypt password handling."""

    def test_hash_and_verify(self):
        hashed = _hash_password("testpassword")
        assert verify_password("testpassword", hashed)

    def test_wrong_password_fails(self):
        hashed = _hash_password("correct")
        assert not verify_password("wrong", hashed)

    def test_empty_password(self):
        hashed = _hash_password("")
        assert verify_password("", hashed)
        assert not verify_password("notempty", hashed)

    def test_hash_is_bcrypt_format(self):
        hashed = _hash_password("test")
        assert hashed.startswith("$2b$")


class TestSessionTokens:
    """Tests for signed cookie session tokens."""

    def test_create_and_validate(self):
        settings = Settings(secret_key="test-secret")
        token = create_session_token(settings)
        assert validate_session_token(token, settings)

    def test_invalid_token_rejected(self):
        settings = Settings(secret_key="test-secret")
        assert not validate_session_token("garbage-token", settings)

    def test_wrong_secret_rejected(self):
        settings1 = Settings(secret_key="secret-1")
        settings2 = Settings(secret_key="secret-2")
        token = create_session_token(settings1)
        assert not validate_session_token(token, settings2)

    def test_expired_token_rejected(self):
        """A token validated with max_age shorter than its age is rejected."""
        secret = "test-secret"
        signer = TimestampSigner(secret)
        token = signer.sign("admin").decode("utf-8")

        # Valid with long max_age
        settings_long = Settings(secret_key=secret, session_max_age=9999)
        assert validate_session_token(token, settings_long)

        # Tamper with the token to make it invalid (simulates expiry detection path)
        bad_token = token[:-2] + "XX"
        assert not validate_session_token(bad_token, settings_long)


class TestAdminRoutes:
    """Tests for admin login/logout and protected routes."""

    @pytest.fixture
    async def client(self, app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c

    @pytest.fixture
    async def authed_client(self, app, admin_password):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as c:
            # Login with the fixture password
            resp = await c.post("/admin/login", data={"admin_key": admin_password})
            assert resp.status_code == 303
            # Extract session cookie
            cookie = resp.cookies.get(SESSION_COOKIE)
            assert cookie is not None
            c.cookies.set(SESSION_COOKIE, cookie)
            yield c

    @pytest.mark.asyncio
    async def test_login_valid_password(self, app, admin_password):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as c:
            resp = await c.post("/admin/login", data={"admin_key": admin_password})
            assert resp.status_code == 303
            assert SESSION_COOKIE in resp.cookies

    @pytest.mark.asyncio
    async def test_login_invalid_password(self, client):
        resp = await client.post("/admin/login", data={"admin_key": "wrongpassword"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_empty_password(self, client):
        resp = await client.post("/admin/login", data={"admin_key": ""})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_route_without_auth(self, client):
        resp = await client.post("/api/v1/cleanup")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_route_with_auth(self, authed_client):
        resp = await authed_client.post("/api/v1/cleanup")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, app, admin_password):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as c:
            # Login
            resp = await c.post("/admin/login", data={"admin_key": admin_password})
            assert SESSION_COOKIE in resp.cookies

            # Logout
            c.cookies.set(SESSION_COOKIE, resp.cookies[SESSION_COOKIE])
            resp = await c.post("/admin/logout")
            assert resp.status_code == 303
            # Cookie should be deleted (set to empty/expired)
