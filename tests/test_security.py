"""Tests for authentication, sessions, and admin routes."""

import json
import logging
import re
import time

import pytest
import yaml
from httpx import ASGITransport, AsyncClient
from itsdangerous import TimestampSigner

from mtfwbuilder.auth import (
    SESSION_COOKIE,
    create_session_token,
    validate_session_token,
    verify_password,
)
from mtfwbuilder.config import Settings, _hash_password, load_settings
from tests.conftest import sign_in_admin


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


GENERATED = re.compile(r"Generated admin password: (\S+) ")


class TestGeneratedAdminPassword:
    """Tests for the admin password that the first start generates (FR-platform-modernization-17)."""

    @pytest.fixture
    def data_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MTFW_BASE_DIR", str(tmp_path))
        monkeypatch.setenv("MTFW_DATA_DIR", str(tmp_path))
        monkeypatch.delenv("MTFW_ADMIN_PASSWORD_HASH", raising=False)
        return tmp_path

    @staticmethod
    def _generated(caplog) -> list[str]:
        return [m.group(1) for r in caplog.records if (m := GENERATED.search(r.getMessage()))]

    def test_first_start_logs_a_generated_password_once_and_stores_only_its_hash(self, data_dir, caplog):
        with caplog.at_level(logging.WARNING, logger="mtfwbuilder"):
            settings = load_settings()

        [password] = self._generated(caplog)
        stored = yaml.safe_load((data_dir / "config.yaml").read_text())
        assert verify_password(password, stored["admin_password_hash"])
        assert stored["admin_password_hash"] == settings.admin_password_hash
        assert password not in (data_dir / "config.yaml").read_text()
        assert not verify_password("meshtastic", settings.admin_password_hash)

    def test_second_start_keeps_the_hash_and_logs_no_password(self, data_dir, caplog):
        first = load_settings()
        caplog.clear()

        with caplog.at_level(logging.WARNING, logger="mtfwbuilder"):
            second = load_settings()

        assert self._generated(caplog) == []
        assert second.admin_password_hash == first.admin_password_hash

    def test_a_password_in_config_yaml_is_used_and_no_password_is_generated(self, data_dir, caplog):
        (data_dir / "config.yaml").write_text(yaml.safe_dump({"admin_password": "chosen-password"}))

        with caplog.at_level(logging.WARNING, logger="mtfwbuilder"):
            settings = load_settings()

        assert self._generated(caplog) == []
        assert verify_password("chosen-password", settings.admin_password_hash)

    def test_a_hash_in_the_environment_is_used_and_no_password_is_generated(self, data_dir, monkeypatch, caplog):
        hashed = _hash_password("env-password")
        monkeypatch.setenv("MTFW_ADMIN_PASSWORD_HASH", hashed)

        with caplog.at_level(logging.WARNING, logger="mtfwbuilder"):
            settings = load_settings()

        assert self._generated(caplog) == []
        assert settings.admin_password_hash == hashed

    def test_a_config_json_without_a_password_gets_a_generated_password(self, data_dir, caplog):
        (data_dir / "config.json").write_text(json.dumps({}))

        with caplog.at_level(logging.WARNING, logger="mtfwbuilder"):
            settings = load_settings()

        [password] = self._generated(caplog)
        assert verify_password(password, settings.admin_password_hash)


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
        """A token older than session_max_age is rejected."""
        secret = "test-secret"
        signed_at = int(time.time()) - 10_000

        class PastSigner(TimestampSigner):
            # The token gets a fixed timestamp 10,000 seconds in the past, so its age
            # is always larger than the 3,600-second limit below.
            def get_timestamp(self) -> int:
                return signed_at

        token = PastSigner(secret).sign("admin").decode("utf-8")

        # A limit larger than the age accepts the token, so only the age causes the rejection.
        assert validate_session_token(token, Settings(secret_key=secret, session_max_age=99_999))
        assert not validate_session_token(token, Settings(secret_key=secret, session_max_age=3600))

    def test_tampered_signature_rejected(self):
        """A token with one changed signature character is rejected."""
        settings = Settings(secret_key="test-secret")
        token = create_session_token(settings)
        value, _, signature = token.rpartition(".")

        # A middle character carries 6 full bits of the signature. The last character
        # carries unused padding bits, so a change there can decode to the same bytes.
        middle = len(signature) // 2
        replacement = "B" if signature[middle] == "A" else "A"
        bad_token = f"{value}.{signature[:middle]}{replacement}{signature[middle + 1 :]}"

        assert bad_token != token
        assert not validate_session_token(bad_token, settings)


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
            token = sign_in_admin(c, app.state.settings)
            c.cookies.delete(SESSION_COOKIE)
            resp = await c.post("/admin/login", data={"admin_key": admin_password, "csrf_token": token})
            assert resp.status_code == 303
            # Extract session cookie
            cookie = resp.cookies.get(SESSION_COOKIE)
            assert cookie is not None
            c.cookies.set(SESSION_COOKIE, cookie)
            c.token = sign_in_admin(c, app.state.settings)
            yield c

    @pytest.mark.asyncio
    async def test_login_valid_password(self, app, admin_password):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as c:
            token = sign_in_admin(c, app.state.settings)
            c.cookies.delete(SESSION_COOKIE)
            resp = await c.post("/admin/login", data={"admin_key": admin_password, "csrf_token": token})
            assert resp.status_code == 303
            assert SESSION_COOKIE in resp.cookies

    @pytest.mark.asyncio
    async def test_login_invalid_password(self, app, client):
        token = sign_in_admin(client, app.state.settings)
        resp = await client.post("/admin/login", data={"admin_key": "wrongpassword", "csrf_token": token})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_empty_password(self, app, client):
        token = sign_in_admin(client, app.state.settings)
        resp = await client.post("/admin/login", data={"admin_key": "", "csrf_token": token})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_route_without_auth(self, client):
        resp = await client.post("/api/v1/cleanup")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_protected_route_with_auth(self, authed_client):
        resp = await authed_client.post("/api/v1/cleanup", data={"csrf_token": authed_client.token})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True

    @pytest.mark.asyncio
    async def test_logout_clears_session(self, app, admin_password):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as c:
            # Login
            token = sign_in_admin(c, app.state.settings)
            c.cookies.delete(SESSION_COOKIE)
            resp = await c.post("/admin/login", data={"admin_key": admin_password, "csrf_token": token})
            assert SESSION_COOKIE in resp.cookies

            # Logout
            c.cookies.set(SESSION_COOKIE, resp.cookies[SESSION_COOKIE])
            token = sign_in_admin(c, app.state.settings)
            resp = await c.post("/admin/logout", data={"csrf_token": token})
            assert resp.status_code == 303
            # Cookie should be deleted (set to empty/expired)
